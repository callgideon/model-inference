"""E5L judge scenarios: the J2 judge DRY RUN (no external provider: J2's local judge fake on
127.0.0.1:57165; P-10 absent) over the real L2 access, the real trace projection and objects
and J2's in-memory D6J ledger (D6J unmerged: o04's ledger case is NOT RUN). Faults: a grant
revoked mid-queue, content expired or deleted before egress, a submit that times out, a
worker restarted mid-submit. Proofs: no unconsented egress, no duplicate paid submit, no
customer-wallet charge. Run only through `runner.py`.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import observe_world as ow                              # noqa: E402

world, run = ow.world, ow.run
CONTENT = b'{"q":"Describe the van."}'


def traced(trip, count: int, tag: str) -> list[str]:
    alpha = trip.world.alpha
    ids = [ow.served(trip, alpha, f"{tag}-{n}") for n in range(count)]
    ow.captured(trip, alpha, ids, CONTENT)
    assert ow.shipped(trip).rows == count
    return ids


def books(trip) -> tuple:
    """Every CREDIT wallet's ledger and reservation, and the USD wallets: what a judge run
    must never move."""
    return (tuple(trip.wallet(t) for t in (trip.world.alpha, trip.world.beta)),
            trip.db("select org_id, ledger_total, reserved_total from infrx.wallets "
                    "order by org_id"))


# --- o04 the consented dry run -------------------------------------------------------------
def test_o04_the_default_dry_run_mode_sends_nothing(workdir):
    from infrx.contracts import errors
    from infrx.contracts.limits import DEFAULTS
    with ow.observe_trip(workdir) as trip:
        ids = traced(trip, 1, "o04-default")
        with ow.judge(trip, mode=DEFAULTS.judge_mode) as judge:
            try:
                run(ow.submit(judge, trip, ids))
                raise AssertionError("the default judge mode submitted")
            except errors.BudgetExceeded:
                pass
            assert judge.posts() == [] and judge.ledger.runs == {}


def test_o04_a_consented_run_sends_once_scores_once_and_charges_no_customer_wallet(workdir):
    from infrx.judge.submit import collect
    with ow.observe_trip(workdir) as trip:
        ids = traced(trip, 2, "o04")
        before = books(trip)
        with ow.judge(trip) as judge:
            job = judge.job(trip, ids)
            run_ = run(ow.submit(judge, trip, ids, job=job))
            assert run_.state == "submitted", run_
            [post] = judge.posts()
            assert sorted(item["sample_id"] for item in post["items"]) == sorted(ids)
            assert {item["content"] for item in post["items"]} == {CONTENT.decode()}
            judge.fake.outputs[run_.external_id] = [[sid, ow.judge_result()] for sid in ids]
            done = run(collect(job.run_id, wiring=judge.wiring))
            assert done.state == "completed" and done.actual is not None, done
            assert len(judge.ledger.results) == len(ids)
            assert run(collect(job.run_id, wiring=judge.wiring)).state == "completed"
            assert len(judge.ledger.results) == len(ids), "a duplicate delivery scored twice"
            assert judge.ledger.spent == {judge.payer: done.actual}, "not the named payer"
            assert len(judge.posts()) == 1
        assert books(trip) == before, "a judge run moved a customer wallet"
        trip.conserved(trip.world.alpha)


def test_o04_the_judge_ledger_is_d6js_postgresql_ledger(workdir):
    ow.not_run("o04", "D6J", "J3", why="the judge ledger here is J2's in-memory D6J fake "
               "(tests/j/submit/fakes.py); reservation races under real PostgreSQL contention "
               "and J3's calibration need D6J/J3 merged")


# --- o05 a grant revoked mid-queue -------------------------------------------------------------
def test_o05_a_grant_revoked_between_reservation_and_egress_sends_nothing(workdir):
    from infrx.contracts import errors
    with ow.observe_trip(workdir) as trip:
        ids = traced(trip, 1, "o05")
        with ow.judge(trip) as judge:
            ledger = judge.ledger
            begin = ledger.begin_submit

            async def revoked_while_queued(run_id):
                answer = await begin(run_id)
                ow.revoke(trip, trip.world.alpha)             # the owner revokes, mid-queue
                return answer
            ledger.begin_submit = revoked_while_queued
            job = judge.job(trip, ids)
            try:
                run(ow.submit(judge, trip, ids, job=job))
                raise AssertionError("a revoked grant still submitted")
            except (errors.Forbidden, errors.NotFound):
                pass
            assert judge.posts() == [], "content left after the revocation"
            assert ledger.runs[job.run_id].state == "failed"
            assert ledger.committed(judge.payer).is_zero, "the hold was kept"


# --- o06 content expired or deleted before egress --------------------------------------------
def test_o06_content_expired_or_deleted_before_egress_never_reaches_the_judge(workdir):
    """The owner deletes one request and the other's content passes its bound; the sweep has
    not run yet. Oracle: J2 reading the raw projection sends what every read already hides."""
    from infrx.traces.retention import Retention
    with ow.observe_trip(workdir) as trip:
        alpha = trip.world.alpha
        expiring, deleted = traced(trip, 2, "o06")
        live = trip.traces.retention
        later = Retention(live.store, live.traces, live.feedback, live.objects,
                          content_days=live.content_days, metadata_months=live.metadata_months,
                          clock=lambda: ow.Wall.now() + timedelta(days=live.content_days + 1))
        assert run(later.expire()) >= 1
        run(live.delete(alpha.org_id, deleted, "owner"))
        with ow.judge(trip) as judge:
            job = judge.job(trip, [expiring, deleted])
            outcome = run(ow.submit(judge, trip, [expiring, deleted], job=job))
            sent = [item["sample_id"] for post in judge.posts() for item in post["items"]]
            assert sent == [], f"expired/deleted content reached the judge: {sent}"
            assert outcome.state == "failed", outcome


def test_o06_after_the_sweep_nothing_is_left_to_send(workdir):
    from infrx.traces.retention import Retention
    with ow.observe_trip(workdir) as trip:
        alpha = trip.world.alpha
        expiring, deleted = traced(trip, 2, "o06-swept")
        live = trip.traces.retention
        later = Retention(live.store, live.traces, live.feedback, live.objects,
                          content_days=live.content_days, metadata_months=live.metadata_months,
                          clock=lambda: ow.Wall.now() + timedelta(days=live.content_days + 1))
        run(later.expire())
        run(live.delete(alpha.org_id, deleted, "owner"))
        assert run(later.sweep())["failed"] == 0
        with ow.judge(trip) as judge:
            outcome = run(ow.submit(judge, trip, [expiring, deleted]))
            assert judge.posts() == [] and outcome.state == "failed", outcome
            assert judge.ledger.committed(judge.payer).is_zero


# --- o08 a submit timed out, the worker restarted ---------------------------------------------
def test_o08_a_timed_out_submit_is_quarantined_never_resent_and_reconciled(workdir):
    from infrx.judge.submit import collect, reconcile
    with ow.observe_trip(workdir) as trip:
        ids = traced(trip, 1, "o08")
        before = books(trip)
        with ow.judge(trip, timeout_s=1.0) as judge:
            judge.fake.mode, judge.fake.delay_s = "slow", 3.0
            job = judge.job(trip, ids)
            first = run(ow.submit(judge, trip, ids, job=job))
            assert first.state == "ambiguous", first
            assert not judge.ledger.committed(judge.payer).is_zero, "hold released on doubt"
            judge.fake.mode = "ok"
            again = run(ow.submit(judge, trip, ids, job=job))      # a restarted worker
            assert again.state == "ambiguous" and len(judge.posts()) == 1, \
                "an ambiguous submit was sent again"
            settled = run(reconcile(job.run_id, wiring=judge.wiring))
            assert settled.state == "submitted", settled
            judge.fake.outputs[settled.external_id] = [[ids[0], ow.judge_result()]]
            assert run(collect(job.run_id, wiring=judge.wiring)).state == "completed"
            assert len(judge.posts()) == 1 and len(judge.fake.batches) == 1
        assert books(trip) == before
