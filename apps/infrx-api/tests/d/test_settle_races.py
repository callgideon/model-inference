#!/usr/bin/env python3
"""D5 item 6: the settlement races under REAL PostgreSQL transactions (both images).

As D3's `test_lease_races.py`: LOCK-STEP, both orders where there are two (the first
transaction stays OPEN, the second must be seen waiting on a lock - or, for the reaper,
must return at once, because it takes job rows SKIP LOCKED - then the first commits), and
STRESS (callers released together by a barrier). Every boundary is called as
`service_role` with the adapter's arguments, and every race ends in `assert_no_drift`.

    INFRX_D_TASK=d5 uv run --frozen pytest -q tests/d/test_settle_races.py
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest
from infrx.contracts.limits import DEFAULTS
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_credit as cc
from . import checks_leases as cl
from . import checks_settle as cs
from . import pgharness
from .checks_leases import lockstep, rpc
from .test_lease_races import together

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_settle_races"
ROUNDS, CALLERS = 5, 8
_state: dict = {}


class Rig:
    """One migrated database with the admission scenario (both regimes), committed rows."""

    def __init__(self) -> None:
        if "owner" not in _state:
            pgharness.ensure()
            pgharness.recreate(DB)
            pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
            owner = pgharness.connect(DB)
            ca.seed_admission(owner)
            _state["owner"] = owner
        self.owner = _state["owner"]
        self.world = ca.World(self.owner)

    def service(self):
        return cs.service(pgharness.connect, DB)

    def advance(self, seconds: float) -> None:
        cs.advance(self.owner, seconds)

    def running(self, worker: str = "w1", **kw):
        return cl.running(self.owner, self.world, worker, **kw)

    def credit_running(self, worker: str = "wc"):
        return cl.credit_running(self.owner, self.world, worker)

    def complete(self, lease, proposal, regime: str = "legacy_usd"):
        return lambda c: rpc(c, "terminalize", cs.terminalize_args(lease, proposal, regime))

    def cancel(self, request, cause: str):
        handle = cl.row(self.owner, request.request_id)["job_handle"]
        return lambda c: rpc(c, "cancel", {"org_id": request.org_id, "job_handle": handle,
                                           "cause": cause, "limits": cl.LIMITS})

    def proposal(self, request, usage=(1200, 340)):
        return cs.propose(request.request_id, usage=usage, ref=cs.stored(self.owner,
                                                                          request.request_id))

    def debits(self, request) -> int:
        return len(cs.ledger(self.owner, request.request_id)) + \
            len(cs.credit_ledger(self.owner, request.request_id))

    def drift(self, what: str) -> None:
        cs.assert_no_drift(self.owner, what)


# --------------------------------------------------------------------- duplicate complete
def test_race__a_duplicate_completion_debits_once_and_replays() -> None:
    """DUR-SETTLE / DUR-OUTPUT (lost terminal ack): the identical completion behind the
    winner waits on the job row, then REPLAYS the committed outcome; a different one is
    `already_terminal`. Under a barrier of identical and different proposals exactly one
    debit exists and every identical caller answers the same outcome."""
    rig = Rig()
    request, lease = rig.running()
    proposal = rig.proposal(request)
    first, second = lockstep(rig.owner, (rig.service(), rig.complete(lease, proposal)),
                             (rig.service(), rig.complete(lease, proposal)))
    assert first[0] is None and second[0] is None, (first[0], second[0])
    assert second[1]["outcome"] == first[1]["outcome"], "the duplicate did not replay"
    assert rig.debits(request) == 1
    other, other_lease = rig.running("w2")
    same = rig.proposal(other)
    first, second = lockstep(rig.owner, (rig.service(), rig.complete(other_lease, same)),
                             (rig.service(), rig.complete(other_lease, cs.propose(
                                 other.request_id, usage=(9, 9), ref=same["result_ref"]))))
    assert first[0] is None and second[0] == "already_terminal", (first[0], second[0])
    for _ in range(ROUNDS):
        request, lease = rig.running("ws")
        proposal = rig.proposal(request)
        different = cs.propose(request.request_id, usage=(7, 7), ref=proposal["result_ref"])
        answers = together([(rig.service(), rig.complete(lease, proposal))
                            for _ in range(CALLERS // 2)] +
                           [(rig.service(), rig.complete(lease, different))
                            for _ in range(CALLERS // 2)])
        won = {a["outcome"]["usage"]["prompt_tokens"] for code, a in answers if code is None}
        assert len(won) == 1 and rig.debits(request) == 1, (won, answers)
        assert all(code in (None, "already_terminal") for code, _ in answers), answers
        winners = [a["outcome"] for code, a in answers if code is None]
        assert all(w == winners[0] for w in winners), "two outcomes for one job"
    rig.drift("duplicate completions")


# --------------------------------------------------------------------- cancel vs complete
@pytest.mark.parametrize("cause", ("client_cancelled", "client_disconnected", "sync_deadline"))
def test_race__cancel_and_complete_have_one_winner(cause) -> None:
    """DUR-SETTLE: complete first -> the waiting cancel answers `completed` (a completed job
    stays completed); cancel first -> the waiting completion is `already_terminal`, the
    cause recorded, nothing debited. Under a barrier one outcome, at most one debit."""
    rig = Rig()
    request, lease = rig.running()
    first, second = lockstep(rig.owner, (rig.service(), rig.complete(lease, rig.proposal(request))),
                             (rig.service(), rig.cancel(request, cause)))
    assert first[0] is None and second[1]["cause"] == "completed", (first, second)
    assert rig.debits(request) == 1
    other, other_lease = rig.running("w2")
    first, second = lockstep(rig.owner, (rig.service(), rig.cancel(other, cause)),
                             (rig.service(), rig.complete(other_lease, rig.proposal(other))))
    assert first[1]["cause"] == cause and second[0] == "already_terminal", (first, second)
    assert rig.debits(other) == 0
    for _ in range(ROUNDS):
        job, job_lease = rig.running("ws")
        proposal = rig.proposal(job)
        answers = together([(rig.service(), rig.cancel(job, cause))] * 1 +
                           [(rig.service(), rig.complete(job_lease, proposal))] * 1)
        final = cl.row(rig.owner, job.request_id)["outcome_cause"]
        assert final in ("completed", cause) and rig.debits(job) == \
            (1 if final == "completed" else 0), (final, answers)
    rig.drift(f"cancel({cause}) vs complete")


# --------------------------------------------------------------------- complete vs recover
def test_race__the_reaper_skips_a_settling_job_and_never_reterminalizes_it() -> None:
    """DUR-OUTPUT / DUR-FENCE: a settlement in flight is invisible to the reaper (SKIP
    LOCKED: it returns at once, the job untouched); after the commit the reaper finds a
    terminal job and produces nothing for it. The other order: the reaper's requeue of a
    lapsed lease commits first and the completion behind it is `stale_lease`."""
    rig = Rig()
    request, lease = rig.running()
    rig.advance(DEFAULTS.lease_ttl_s - 1)
    settler, reaper = rig.service(), rig.service()
    settler.execute("begin")
    try:
        code, _ = rig.complete(lease, rig.proposal(request))(settler)
        assert code is None, code
        rig.advance(2)                                # the lease is past its expiry now
        started = time.monotonic()
        code, produced = rpc(reaper, "recover", {"limits": cl.LIMITS})
        assert time.monotonic() - started < 5, "the reaper waited on a settling job"
        assert request.request_id not in str(produced), f"the reaper touched it: {produced}"
    finally:
        settler.execute("commit")
    code, produced = rpc(reaper, "recover", {"limits": cl.LIMITS})
    assert request.request_id not in str(produced), "the reaper re-terminalized a settled job"
    assert cl.row(rig.owner, request.request_id)["outcome_cause"] == "completed"
    lost, lost_lease = rig.running("w-same")
    rig.advance(DEFAULTS.lease_ttl_s)
    first, second = lockstep(rig.owner, (rig.service(), lambda c: rpc(c, "recover", {
        "limits": cl.LIMITS})), (rig.service(), rig.complete(lost_lease, rig.proposal(lost))))
    assert second[0] == "stale_lease", second
    rig.drift("complete vs recover")


# --------------------------------------------------------------------- the 24 h release
def test_race__reconcile_and_the_reaper_release_once_never_a_debit() -> None:
    """02: the 24 h release happens once. `reconcile` first (the job row held): the reaper
    returns at once and releases nothing for it; after the commit the hold is released,
    audited, the ledger unmoved. The reaper first: the reconcile behind it waits on the row
    and answers the state the reaper left - one release, one release projection. An
    identical late completion of a released job replays; a different one is refused; no
    money moves. (One due job at a time: the reaper releasing another job's hold on the
    same wallet would rightly wait on the wallet row the open reconcile holds.)"""
    rig = Rig()

    def unknown(worker):
        request, lease = rig.credit_running(worker)
        cl.publish(rig.owner, lease)
        proposal = cs.propose(request.request_id, "client_disconnected", "failed")
        assert rig.complete(lease, proposal, "credit")(rig.service())[0] is None
        rig.advance(DEFAULTS.unknown_usage_reconcile_s)
        return request, lease, proposal

    def reconcile(request, op):
        return lambda c: rpc(c, "reconcile", {"org_id": request.org_id, "operation_id": op,
                                              "request_id": request.request_id,
                                              "actor": "ops@test", "at": None})
    one, one_lease, one_proposal = unknown("r1")
    wallet = cs.credit(rig.owner, one.request_id)
    maximum = cl.row(rig.owner, one.request_id)["maximum_hold"]
    holder, reaper = rig.service(), rig.service()
    holder.execute("begin")
    code, _ = reconcile(one, "race-1")(holder)
    assert code is None, code
    try:
        started = time.monotonic()
        code, produced = rpc(reaper, "recover", {"limits": cl.LIMITS})
        assert time.monotonic() - started < 5 and one.request_id not in str(produced), produced
    finally:
        holder.execute("commit")
    two, _two_lease, _two_proposal = unknown("r2")
    first, second = lockstep(rig.owner, (rig.service(), lambda c: rpc(c, "recover", {
        "limits": cl.LIMITS})), (rig.service(), reconcile(two, "race-2")))
    assert two.request_id in str(first[1]), f"the reaper did not release it: {first}"
    assert second[0] is None and second[1]["settlement_state"] == \
        "released_platform_absorbed", second
    for request in (one, two):
        assert cs.credit_hold(rig.owner, request.request_id) == "released"
        releases = [k for k in cl.kinds(rig.owner, request.request_id)
                    if k == "usage_projection"]
        assert len(releases) == 2, f"not exactly one release projection: {releases}"
    after = cs.credit(rig.owner, one.request_id)
    assert after == (wallet[0], wallet[1] - maximum), (wallet, after)
    assert rig.complete(one_lease, one_proposal, "credit")(rig.service())[0] is None
    assert rig.complete(one_lease, rig.proposal(one, (1, 1)), "credit")(
        rig.service())[0] == "already_terminal"
    assert cs.credit(rig.owner, one.request_id) == after and rig.debits(one) == 0
    rig.drift("reconcile vs the reaper")


# --------------------------------------------------------------------- one CREDIT wallet
def test_race__two_settlements_and_an_adjustment_on_one_credit_wallet() -> None:
    """CREDIT-SPEND / DUR-CAP: two jobs settling on one CREDIT wallet while an operator
    adjustment lands, released together: exact totals (the two admitted-card charges and the
    adjustment, reservations back), nothing negative available, every summary reconciled."""
    rig = Rig()
    for round_ in range(ROUNDS):
        a, a_lease = rig.credit_running(f"a{round_}")
        b_, b_lease = rig.credit_running(f"b{round_}")
        wid = cl.row(rig.owner, a.request_id)["wallet_id"]
        before = cs.credit(rig.owner, a.request_id)
        held = sum(cl.row(rig.owner, r.request_id)["maximum_hold"] for r in (a, b_))
        answers = together([
            (rig.service(), rig.complete(a_lease, rig.proposal(a, (1200, 340)), "credit")),
            (rig.service(), rig.complete(b_lease, rig.proposal(b_, (10, 1)), "credit")),
            (rig.service(), lambda c: rpc(c, "grant_credit", {
                "wallet_id": str(wid), "kind": "operator_adjustment", "amount": "-1.00000000",
                "operation_id": f"00000000-0000-4000-8000-{round_:012d}", "actor": "ops@test",
                "reason": "race", "at": None}))])
        assert all(code is None for code, _ in answers), answers
        charged = sum(Decimal(a["charged_credits"]) for _, a in answers[:2])
        want = cs._admission_v2(rig.owner, a.request_id).rate_card
        assert charged == want.debit(1200, 340).raw("CREDIT") + want.debit(10, 1).raw("CREDIT")
        after = cs.credit(rig.owner, a.request_id)
        assert after == (before[0] - charged - 1, before[1] - held), (before, after)
        assert after[0] - after[1] >= 0, "negative available"
    rig.drift("one CREDIT wallet")


# --------------------------------------------------------------------- stale generation
def test_race__a_stale_generation_after_a_requeue_cannot_settle() -> None:
    """DUR-FENCE (dr05 on the settlement): generation 1's completion waiting behind the
    reaper's requeue is `stale_lease` once it commits; the SAME worker id then claims
    generation 2, whose completion settles; generation 1's is still `stale_lease`."""
    rig = Rig()
    request, lease = rig.running("w-same")
    rig.advance(DEFAULTS.lease_ttl_s)
    proposal = rig.proposal(request)
    first, second = lockstep(rig.owner, (rig.service(), lambda c: rpc(c, "recover", {
        "limits": cl.LIMITS})), (rig.service(), rig.complete(lease, proposal)))
    assert second[0] == "stale_lease", second
    code, answer = rpc(rig.service(), "claim", {"job_id": request.request_id,
                                                "worker_id": "w-same", "limits": cl.LIMITS})
    assert code is None, code
    renewed = cl.lease_of(answer)
    assert renewed.generation == 2, renewed
    assert rig.complete(lease, proposal)(rig.service())[0] == "stale_lease"
    code, doc = rig.complete(renewed, proposal)(rig.service())
    assert code is None and doc["outcome"]["settlement_state"] == "settled", (code, doc)
    assert rig.debits(request) == 1
    rig.drift("a stale generation")


def test_races__the_mutants_concurrency_check() -> None:
    """The check the migration mutants run (`settle_races`), on this module's database."""
    Rig()
    print(cs.check_settle_races(pgharness.connect, DB))
