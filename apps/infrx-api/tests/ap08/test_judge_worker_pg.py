"""AP-08 08c/08d: the judge worker's START step composed on ap8 - a run queued through the
doors (as `POST /lab/v1/judge/runs` queues it) becomes J2's `submit` against the LOCAL judge
fake (`tests/j/submit/judge_fake.py` on ap8's judge-fake port 57564), on PostgreSQL's D6J
ledger. Frozen sample, reservation before send, one send across restarts, ambiguous never
resent, budget exhaustion, cancel, revocation and dry-run all send nothing they must not; the
consumer's CREDIT never moves. Test rates (`tests/j/fakes.TEST_RATES`), never an approved price.

    INFRX_D_TASK=ap8 uv run --frozen pytest -q tests/ap08/test_judge_worker_pg.py
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from infrx.contracts.limits import DEFAULTS
from infrx.contracts.tasklocal import local_services
from infrx.judge import start
from infrx.judge.submit import HttpJudgeProvider, JudgeWiring
from infrx.lab.access import LabAccess
from infrx.media.store import InMemoryObjectStore
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_consent import PgJudgeLedger
from tests.d import pgharness
from tests.d import test_d6j_doors as jd
from tests.d import test_d6j_judge as j
from tests.d import test_l2sql_access as l2
from tests.j import fakes as j1
from tests.j.submit import fakes
from tests.j.submit.judge_fake import JudgeFake

from .conftest import pg_reason
from .conftest import seed as seed  # noqa: PLC0414 - the world `code_mutants_d7.kill` seeds
from .test_judge_doors_pg import doors

pytestmark = pytest.mark.skipif(pg_reason() is not None, reason=f"{pg_reason()}")

NEMO, C1, DEV = j.NEMO, j.C1, l2.DEV
FAKE_PORT = local_services("ap8")["judge-fake"].host_port
LIVE = DEFAULTS.replace(judge_mode="live")
TRACES = tuple(fakes.rid(n) for n in (11, 12, 13))


def run(coro):
    return asyncio.run(coro)


class Worker:
    """The judge role's start + collect over this database, the judge fake and three of C1's
    stored traces (the first with video)."""

    def __init__(self, conn, fake: JudgeFake, *, settings=LIVE, timeout_s: float = 5.0) -> None:
        self.conn, self.fake = conn, fake
        self.grantor = l2.org(conn, C1)
        connect = connector(pgharness.dsn(conn.info.dbname))
        projection, objects = fakes.Projection(), InMemoryObjectStore()
        for request_id in TRACES:
            run(fakes.trace(projection, objects, self.grantor, request_id))
        self.ledger = PgJudgeLedger(connect)
        self.queued = start.pg_queued(connect)
        self.wiring = JudgeWiring(access=LabAccess(PgAccessStore(connect)), ledger=self.ledger,
                                  provider=HttpJudgeProvider(fake.url, timeout_s=timeout_s),
                                  retention=fakes.retention(projection, objects),
                                  rates=j1.TEST_RATES, settings=settings,
                                  rubric_of=start.pg_rubric_of(connect))

    async def eligible(self, grantor: str, model: str, limit: int):
        assert (grantor, model) == (self.grantor, l2.MODEL)
        return [(t, n == 0) for n, t in enumerate(TRACES)][:limit]

    def request(self, key: str, samples: int = 2) -> str:
        """A run queued as the HTTP family queues it: the keyed config, then the request."""
        d = doors(self.conn)
        config = f"0c000000-0000-4000-8000-{samples:012x}"
        run(d.call(DEV, "lab_judge_configure_keyed", NEMO, config, self.grantor, l2.MODEL,
                   j1.JUDGE_MODEL, 1, samples))
        run_id = f"7a000000-0000-4000-8000-{int(key):012x}"
        run(d.call(DEV, "lab_judge_request_run", NEMO, run_id, config, j.PAYER))
        return run_id

    def start_pass(self) -> dict:
        return run(start.start_pass(self.queued, self.wiring, self.eligible))

    def state(self, run_id: str) -> str | None:
        row = self.conn.execute("select state from infrx.lab_judge_runs where run_id = %s",
                                (run_id,)).fetchone()
        return row and row[0]

    def credit(self):
        return self.conn.execute("select sum(ledger_total), sum(reserved_total) "
                                 "from infrx.credit_wallets").fetchone()


def check_a_queued_run_is_frozen_reserved_and_sent_once(conn, fake) -> None:
    """08c: the start pass reserves the worst case BEFORE the one send, sends exactly the
    frozen sample (seeded by the run id, media marked), and a restart sends nothing more; the
    collect settles once; consumer CREDIT is untouched. Failure oracle: a send before the
    reservation, a second batch after a restart, a sample that moves between passes."""
    w = Worker(conn, fake)
    credit = w.credit()
    run_id = w.request("1")
    assert w.start_pass() == {"started": 1, "waiting": 0, "refused": 0, "failed": 0}
    assert len(fake.posts) == 1 and w.state(run_id) == "submitted"
    reserved, sent, media = conn.execute(
        "select reserved, sent_sample_ids::text[], media_ids::text[] from infrx.lab_judge_runs "
        "where run_id = %s", (run_id,)).fetchone()
    assert reserved > 0 and j.held(conn)[0] == f"{reserved:.8f}"
    frozen = start.frozen_sample(run_id, [(t, n == 0) for n, t in enumerate(TRACES)], 2)
    assert (tuple(sent), frozenset(media)) == (frozen[0], frozen[1])
    assert [i["sample_id"] for i in fake.posts[0]["items"]] == list(sent)
    assert w.start_pass()["started"] == 0 and len(fake.posts) == 1, "a restart sent again"
    assert run(start.pg_queued(connector(pgharness.dsn(conn.info.dbname)))(10)) == []
    assert w.credit() == credit, "provider judging moved consumer CREDIT"


def check_cancel_dry_run_budget_and_revocation_send_nothing(conn, fake) -> None:
    """08d: a cancelled request is never started; dry-run, an exhausted budget and a revoked
    grant reserve and send nothing and the request stays queued (never shown as scored).
    Failure oracle: egress for any of the four."""
    w = Worker(conn, fake)
    cancelled = w.request("2")
    run(doors(conn).call(DEV, "lab_judge_cancel", NEMO, cancelled))
    assert w.start_pass()["started"] == 0 and w.state(cancelled) is None
    queued = w.request("3")
    dry = Worker(conn, fake, settings=DEFAULTS)
    assert dry.start_pass()["refused"] == 1 and dry.state(queued) is None
    j.put_budget(conn, "0.00000001")
    assert w.start_pass()["refused"] == 1 and w.state(queued) is None
    j.put_budget(conn, "100.00000000")
    jd.revoke(conn)
    assert w.start_pass()["refused"] == 1 and w.state(queued) is None
    assert fake.posts == [] and j.held(conn)[0] == "0.00000000"


def check_an_ambiguous_send_is_quarantined_and_never_resent(conn, fake) -> None:
    """08d: a send whose answer is lost (the fake accepts, then answers too late) is
    `ambiguous` with the hold kept; the next start pass does not resend it; the worker's
    reconcile adopts the provider's batch by its submit key. Failure oracle: a second POST."""
    fake.mode, fake.delay_s = "slow", 1.0
    w = Worker(conn, fake, timeout_s=0.3)
    run_id = w.request("4")
    w.start_pass()
    assert w.state(run_id) == "ambiguous" and len(fake.posts) == 1
    fake.mode = "ok"
    assert w.start_pass()["started"] == 0 and len(fake.posts) == 1
    from infrx.lab.workers.__main__ import judge_pass
    done = run(judge_pass(w.wiring, lambda: _async([NEMO])))
    assert done["reconciled"] == 1 and w.state(run_id) in ("submitted", "completed")
    assert len(fake.posts) == 1, "reconciliation resubmitted"


async def _async(value):
    return value


def check_results_are_graded_by_the_runs_rubric_and_calibrated_against_the_gold_set(
        conn, fake) -> None:
    """api-judge-2: the collect pass grades with the rubric the run's configuration pins
    (SR-AP08-1's read), and the gold-set calibration reads only that configuration's
    COMPLETED results under a CURRENT grant - published `insufficient` below MIN_PAIRS.
    Failure oracle: results of an unsettled run, of another judge model or rubric version, or
    after the grant is revoked entering a calibration; a `calibrated` claim on one pair."""
    from infrx.judge.calibration import goldset
    from infrx.lab.workers.__main__ import judge_pass
    from tests.ap08.test_judge_cli_pg import answer
    w = Worker(conn, fake)
    connect = connector(pgharness.dsn(conn.info.dbname))
    results_of = goldset.pg_results_of(connect)
    run_id = w.request("6")          # its frozen sample holds the one trace with video
    w.start_pass()
    gold = goldset.GoldSet.model_validate({
        "provider_org_id": NEMO, "org_id": w.grantor, "judge_model": j1.JUDGE_MODEL,
        "rubric_version": 1, "reviewed_by": "operator@infrx.test", "review_ref": "ap8",
        "labels": [{"sample_id": t, "verdict": "correct"} for t in TRACES]})
    sent = [i["sample_id"] for i in fake.posts[0]["items"]]
    fake.outputs[fake.batches[fake.posts[0]["submit_key"]]] = [answer(t, t == TRACES[0])
                                                               for t in sent]
    provider, real = w.wiring.provider, w.wiring.provider.results

    async def unsettled(external_id):          # the batch answered in part: nothing settled
        return dataclasses.replace(await real(external_id), done=False, cost=None)
    provider.results = unsettled
    run(judge_pass(w.wiring, lambda: _async([NEMO])))
    del provider.results
    assert w.state(run_id) == "submitted" and conn.execute(
        "select count(*) from infrx.lab_judge_results where run_id = %s",
        (run_id,)).fetchone()[0] == len(sent)
    assert run(results_of(gold)) == [], "an unsettled run's results entered a calibration"
    done = run(judge_pass(w.wiring, lambda: _async([NEMO])))
    assert done["collected"] == 1 and w.state(run_id) == "completed"
    rows = run(results_of(gold))
    assert sorted(r["sample_id"] for r in rows) == sorted(sent)
    for other in ({"judge_model": "judge-2"}, {"rubric_version": 2},
                  {"provider_org_id": j.OTHER}):
        assert run(results_of(gold.model_copy(update=other))) == [], other
    quality = run(goldset.calibrate(w.ledger, results_of, gold))
    assert (quality.state, quality.kappa.n) == ("insufficient", 1)
    shown = conn.execute("select calibration->>'state' from infrx.lab_judge_calibrations "
                         "order by calibration_id desc limit 1").fetchone()[0]
    assert shown == "insufficient"
    jd.revoke(conn)
    assert run(results_of(gold)) == [], "results after the grant was revoked"


WORKER = (check_a_queued_run_is_frozen_reserved_and_sent_once,
          check_cancel_dry_run_budget_and_revocation_send_nothing,
          check_an_ambiguous_send_is_quarantined_and_never_resent,
          check_results_are_graded_by_the_runs_rubric_and_calibrated_against_the_gold_set)


def _with_fake(check):
    def run_check(conn) -> None:
        judge = JudgeFake(FAKE_PORT)
        try:
            check(conn, judge)
        finally:
            judge.close()
    return run_check


#: `code_mutants_d7.kill`'s world shape: `check(conn)` with its own judge fake.
CHECKS = {c.__name__: _with_fake(c) for c in WORKER}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_ap08_worker(pg, name) -> None:
    CHECKS[name](pg)
