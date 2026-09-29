#!/usr/bin/env python3
"""D6J / SR-J2-1 (wave-5 LW2, lab-sql; the judge lane's schema request): J2's judge ledger on
real PostgreSQL - JUDGE-BUDGET, JUDGE-SCORES and LAB-ACCESS (the SQL half) for
`0036_lab_judge_ledger.sql`, composed with `PgJudgeLedger`.

World: test_l2sql_access's role matrix (2 consumers, NEMO and OTHER, BOTH = a consumer who is
OTHER's developer) plus C1's external_judging grant to NEMO over request and response content,
the `lab_submission` flag on, and a NEMO payer budget of 100 PROVIDER_USD (0031's
`lab_budgets`: the one cap). Each `check_*` is the check a mutant in `code_mutants_d6j.py`
(`JUDGE`) must break; rolled-back checks leave nothing, the contention drills commit.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d6j_judge.py
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.v2.money_units import ProviderUsd
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_consent import Consent, PgJudgeLedger

from . import checks_credit as cc
from . import pgharness
from . import test_l2sql_access as l2

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d6jj"

NEMO, OTHER, C1, BOTH = l2.NEMO, l2.OTHER, l2.C1, l2.BOTH
call, ok, refusal, rolled_back = l2.call, l2.ok, l2.refusal, l2.rolled_back
PAYER = f"lab:payer:{NEMO}:0000003c-0000-4000-8000-00000000003c@sha256:{'a' * 64}"
PAYER_2 = f"lab:payer:{NEMO}:0000003d-0000-4000-8000-00000000003d@sha256:{'a' * 64}"
OTHER_PAYER = f"lab:payer:{OTHER}:0000003e-0000-4000-8000-00000000003e@sha256:{'a' * 64}"
TABLES = ("lab_judge_runs", "lab_judge_results", "lab_judge_audit")
RPCS = ("lab_judge_reserve", "lab_judge_begin_submit", "lab_judge_record_sent",
        "lab_judge_record_submission", "lab_judge_quarantine", "lab_judge_release",
        "lab_judge_record_results", "lab_judge_settle", "lab_judge_run", "lab_judge_sweep")
W: dict[str, object] = {}
BOTH_CATEGORIES = ["request_content", "response_content"]


def uid(n: int, tag: int = 0x3a) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012x}"


SAMPLES = [uid(i, 0x5a) for i in range(1, 4)]


def judge_grant(conn, **over) -> dict:
    """The next version of C1's grant to NEMO (external judging over both categories)."""
    return call(conn, "lab_put_access_grant", l2.scope(
        conn, **{"purposes": ["external_judging"], "categories": BOTH_CATEGORIES, **over}))


def put_budget(conn, limit: str, payer_ref: str = PAYER, provider: str = NEMO) -> dict:
    return ok(conn, "lab_put_budget", {"provider_org_id": provider, "payer_ref": payer_ref,
                                       "limit": limit, "actor": "ops@nemo", "reason": "q4"})


def reserve(run: str, *, cost: str = "40.00000000", payer: str = PAYER, provider: str = NEMO,
            grant: tuple | None = None, samples: list[str] = SAMPLES) -> dict:
    grant_id, version = grant or (W["grant_id"], W["version"])
    return {"run_id": run, "provider_org_id": provider, "payer_ref": payer,
            "grant_id": grant_id, "grant_version": version, "sample_ids": samples,
            "media_ids": samples[:1], "price_version": "judge-rates-2026-09",
            "max_cost": cost}


def held(conn, payer: str = PAYER) -> tuple[str, str]:
    b = ok(conn, "lab_budget", {"provider_org_id": NEMO, "payer_ref": payer})
    return b["reserved"], b["settled"]


def submitted(conn, run: str, sent: list[str] = SAMPLES, batch: str = "batch-1") -> dict:
    ok(conn, "lab_judge_reserve", reserve(run))
    ok(conn, "lab_judge_begin_submit", {"run_id": run})
    ok(conn, "lab_judge_record_sent", {"run_id": run, "sample_ids": sent})
    return ok(conn, "lab_judge_record_submission", {"run_id": run, "external_id": batch})


def audit(conn, run: str) -> list[str]:
    return [e for (e,) in conn.execute("select event from infrx.lab_judge_audit where run_id = "
                                       "%s order by event_id", (run,)).fetchall()]


def seed(conn) -> None:
    l2.seed(conn)
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('lab_submission', true, 'd6j-test', 'test')")
    g = judge_grant(conn)
    W["grant_id"], W["version"] = g["grant_id"], g["version"]
    other = call(conn, "lab_put_access_grant", l2.scope(
        conn, owner=BOTH, provider=OTHER, model_ids=[l2.OTHER_MODEL],
        purposes=["external_judging"], categories=BOTH_CATEGORIES))
    W["other_grant"] = (other["grant_id"], other["version"])
    put_budget(conn, "100.00000000")
    put_budget(conn, "100.00000000", OTHER_PAYER, OTHER)


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session (a provider member included) reads the ledger or executes
    its RPCs; the platform role reads and writes only through them."""
    probes = [f"select count(*) from infrx.{t}" for t in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached the judge ledger: {reached}"
    for table in TABLES:
        assert cc.refused_as(conn, "service", f"select count(*) from infrx.{table}") is None
        got = cc.refused_as(conn, "service", f"delete from infrx.{table}")
        assert got is not None and got.startswith("42501"), f"service edits {table}: {got}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused"


@rolled_back
def check_the_ledger_is_off_without_its_flag(conn) -> str:
    """09 §D6J: the judge ledger is D6J's `lab_submission` flag (separate from D6F's): off -
    or absent - every write is 55000 and nothing is reserved."""
    ok(conn, "lab_judge_reserve", reserve(uid(1)))
    cc.set_flag(conn, "lab_submission", False)
    got = {name: refusal(conn, name, args) for name, args in (
        ("lab_judge_reserve", reserve(uid(2))),
        ("lab_judge_begin_submit", {"run_id": uid(1)}),
        ("lab_judge_sweep", {"older_than_s": 0}))}
    assert got == dict.fromkeys(got, "dependency_unavailable"), got
    conn.execute("delete from infrx.feature_flags where name = 'lab_submission'")
    assert refusal(conn, "lab_judge_reserve", reserve(uid(2))) == "dependency_unavailable"
    assert held(conn) == ("40.00000000", "0.00000000")
    return "flag off or absent: 55000, nothing moves"


@rolled_back
def check_a_reservation_needs_the_current_external_judging_consent(conn) -> str:
    """LAB-ACCESS: the consent snapshot is the grantor's CURRENT grant version to THIS provider,
    in force, for external_judging over the question AND the answer; anything else -
    an old version, a narrower grant, another purpose, another provider's grant, a revoked
    one - is `consent_missing` and nothing is held."""
    got = {"old_version": refusal(conn, "lab_judge_reserve", reserve(
        uid(3), grant=(W["grant_id"], W["version"] - 1)))}
    got["other_provider"] = refusal(conn, "lab_judge_reserve", reserve(
        uid(3), grant=W["other_grant"]))
    narrower = judge_grant(conn, categories=["request_content"])
    got["question_only"] = refusal(conn, "lab_judge_reserve", reserve(
        uid(3), grant=(narrower["grant_id"], narrower["version"])))
    training = judge_grant(conn, purposes=["training"])
    got["training"] = refusal(conn, "lab_judge_reserve", reserve(
        uid(3), grant=(training["grant_id"], training["version"])))
    current = judge_grant(conn)
    revoked = ok(conn, "lab_revoke_access_grant", {
        "actor_user_id": C1, "grantor_org_id": l2.org(conn, C1),
        "recipient_provider_org_id": NEMO})
    got["revoked"] = refusal(conn, "lab_judge_reserve", reserve(
        uid(3), grant=(revoked["grant_id"], revoked["version"])))
    assert got == dict.fromkeys(got, "consent_missing"), got
    assert held(conn) == ("0.00000000", "0.00000000")
    regranted = judge_grant(conn)
    run = ok(conn, "lab_judge_reserve", reserve(uid(3), grant=(regranted["grant_id"],
                                                               regranted["version"])))
    assert (run["state"], run["grant_version"], held(conn)[0]) == \
        ("prepared", current["version"] + 2, "40.00000000"), run
    return "old/narrower/other purpose/other provider/revoked: consent_missing"


@rolled_back
def check_the_budget_is_the_payers_one_provider_usd_cap(conn) -> str:
    """JUDGE-BUDGET (R159): a reservation holds against the payer's D6J budget - one cap for
    judge runs and external submissions; past it, or a payer with no budget of this
    provider, is `budget_exceeded`; a replayed reservation is the same run and holds once;
    another provider's run id is `not_found`."""
    first = ok(conn, "lab_judge_reserve", reserve(uid(4), cost="60.00000000"))
    assert ok(conn, "lab_judge_reserve", reserve(uid(4), cost="60.00000000")) == first
    assert held(conn)[0] == "60.00000000", "a replay held twice"
    assert refusal(conn, "lab_judge_reserve", reserve(uid(5), cost="50.00000000")) == \
        "budget_exceeded"
    assert refusal(conn, "lab_judge_reserve", reserve(uid(5), payer=PAYER_2)) == \
        "budget_exceeded", "a payer without a budget reserved"
    assert refusal(conn, "lab_judge_reserve", reserve(uid(5), payer=OTHER_PAYER)) == \
        "budget_exceeded", "NEMO reserved against OTHER's payer"
    assert refusal(conn, "lab_judge_reserve", reserve(
        uid(4), provider=OTHER, payer=OTHER_PAYER, grant=W["other_grant"])) == "not_found"
    assert ok(conn, "lab_judge_reserve", reserve(uid(5), cost="40.00000000"))["state"] == \
        "prepared"
    assert held(conn)[0] == "100.00000000"
    for bad in ({"sample_ids": [SAMPLES[0], SAMPLES[0]]}, {"sample_ids": []},
                {"max_cost": "-1"}):
        assert refusal(conn, "lab_judge_reserve", {**reserve(uid(6), cost="0"), **bad}) == \
            "invalid_request", bad
    return "one cap per payer; replay holds once; foreign run id not_found"


@rolled_back
def check_one_intent_then_the_samples_that_left_then_the_ack(conn) -> str:
    """JUDGE-BUDGET (R161): only the call that moved prepared -> submitting may egress; the
    samples that leave are the run's own, recorded once before egress; the ack names one
    batch (the same again is the row, another is `idempotency_conflict`)."""
    ok(conn, "lab_judge_reserve", reserve(uid(7)))
    first = ok(conn, "lab_judge_begin_submit", {"run_id": uid(7)})
    again = ok(conn, "lab_judge_begin_submit", {"run_id": uid(7)})
    assert (first["created"], again["created"], first["run"]["submit_key"]) == \
        (True, False, f"submit:{uid(7)}"), (first, again)
    assert refusal(conn, "lab_judge_record_sent", {"run_id": uid(7), "sample_ids": [
        uid(99, 0x5a)]}) == "state_conflict", "a foreign sample recorded as sent"
    sent = ok(conn, "lab_judge_record_sent", {"run_id": uid(7), "sample_ids": SAMPLES[:2]})
    assert sent["sent_sample_ids"] == SAMPLES[:2], sent
    assert ok(conn, "lab_judge_record_sent", {"run_id": uid(7), "sample_ids": SAMPLES[:2]}) \
        == sent
    assert refusal(conn, "lab_judge_record_sent", {"run_id": uid(7), "sample_ids": SAMPLES}) \
        == "state_conflict", "the sent set rewritten"
    acked = ok(conn, "lab_judge_record_submission", {"run_id": uid(7), "external_id": "b1"})
    assert (acked["state"], acked["external_id"]) == ("submitted", "b1"), acked
    assert ok(conn, "lab_judge_record_submission", {"run_id": uid(7), "external_id": "b1"}) \
        == acked
    assert refusal(conn, "lab_judge_record_submission", {"run_id": uid(7),
                                                         "external_id": "b2"}) == \
        "idempotency_conflict"
    assert refusal(conn, "lab_judge_record_sent", {"run_id": uid(7), "sample_ids": SAMPLES[:2]}
                   ) == "state_conflict", "sent recorded after the ack"
    assert refusal(conn, "lab_judge_begin_submit", {"run_id": uid(99)}) == "not_found"
    return "one intent; sent recorded once, own samples; ack idempotent"


@rolled_back
def check_a_revocation_between_consent_and_submit_stops_egress(conn) -> str:
    """LAB-ACCESS drill: C1 revokes after the reservation and the intent - recording the
    samples about to leave is `consent_missing` (nothing is recorded as sent); a worker that
    dies there leaves `submitting`, which the sweep quarantines (`lease_expired`) for
    reconcile, never a resubmit; releasing it frees the hold."""
    ok(conn, "lab_judge_reserve", reserve(uid(8)))
    ok(conn, "lab_judge_begin_submit", {"run_id": uid(8)})
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1,
                                         "grantor_org_id": l2.org(conn, C1),
                                         "recipient_provider_org_id": NEMO})
    assert refusal(conn, "lab_judge_record_sent", {"run_id": uid(8), "sample_ids": SAMPLES}) \
        == "consent_missing"
    assert ok(conn, "lab_judge_run", {"run_id": uid(8)})["sent_sample_ids"] == []
    assert ok(conn, "lab_judge_sweep", {"older_than_s": 600}) == {"expired": 0}
    conn.execute("update infrx_test.clock set offset_s = offset_s + interval '11 minutes'")
    assert ok(conn, "lab_judge_sweep", {"older_than_s": 600}) == {"expired": 1}
    run = ok(conn, "lab_judge_run", {"run_id": uid(8)})
    assert (run["state"], audit(conn, uid(8))) == ("ambiguous", ["lease_expired"]), run
    assert ok(conn, "lab_judge_begin_submit", {"run_id": uid(8)})["created"] is False
    assert held(conn)[0] == "40.00000000", "the hold of an unknown outcome was dropped"
    ok(conn, "lab_judge_release", {"run_id": uid(8), "state": "failed",
                                   "reason": "no provider record"})
    assert held(conn)[0] == "0.00000000"
    return "revoked: consent_missing before egress; swept to ambiguous; released"


@rolled_back
def check_an_ambiguous_submit_is_quarantined_never_resubmitted(conn) -> str:
    """JUDGE-BUDGET: an unknown submit outcome quarantines the run with its hold (audited);
    nothing may submit it again; it reconciles only to submitted (lookup found the batch) or
    failed (released); a release names a declared move only."""
    ok(conn, "lab_judge_reserve", reserve(uid(9)))
    ok(conn, "lab_judge_begin_submit", {"run_id": uid(9)})
    run = ok(conn, "lab_judge_quarantine", {"run_id": uid(9), "reason": "timeout"})
    assert (run["state"], held(conn)[0], audit(conn, uid(9))) == \
        ("ambiguous", "40.00000000", ["quarantine"])
    assert ok(conn, "lab_judge_begin_submit", {"run_id": uid(9)})["created"] is False
    assert refusal(conn, "lab_judge_quarantine", {"run_id": uid(9), "reason": "x"}) == \
        "state_conflict"
    assert refusal(conn, "lab_judge_release", {"run_id": uid(9), "state": "cancelled",
                                               "reason": "x"}) == "state_conflict"
    assert refusal(conn, "lab_judge_release", {"run_id": uid(9), "state": "submitted",
                                               "reason": "x"}) == "state_conflict", \
        "a release that frees the hold of a submitted batch"
    assert ok(conn, "lab_judge_record_submission", {"run_id": uid(9), "external_id": "b9"}
              )["state"] == "submitted"
    ok(conn, "lab_judge_reserve", reserve(uid(10)))
    assert refusal(conn, "lab_judge_release", {"run_id": uid(10), "state": "failed",
                                               "reason": "x"}) == "state_conflict"
    released = ok(conn, "lab_judge_release", {"run_id": uid(10), "state": "cancelled",
                                              "reason": "operator cancelled"})
    assert (released["state"], held(conn)[0], audit(conn, uid(10))) == \
        ("cancelled", "40.00000000", ["cancelled"])
    again = ok(conn, "lab_judge_release", {"run_id": uid(10), "state": "cancelled",
                                           "reason": "a retried release"})
    assert (again["state"], held(conn)[0], audit(conn, uid(10))) == \
        ("cancelled", "40.00000000", ["cancelled"]), "a repeated release freed another run's hold"
    return "quarantined with its hold; reconciled by lookup; declared releases only, once"


@rolled_back
def check_results_are_stored_once_and_settled_once(conn) -> str:
    """JUDGE-SCORES: results are stored once per (run, sample, rubric version), only for the
    samples that left; settlement is once, within the reservation - an overrun is refused
    WITH a committed audit row and the run stays submitted; the settled actual moves into
    the payer's spent and the hold is freed."""
    submitted(conn, uid(11), sent=SAMPLES[:2])
    rows = [{"sample_id": s, "rubric_version": 1, "accepted": True, "result": {"s": 4}}
            for s in SAMPLES[:2]]
    assert ok(conn, "lab_judge_record_results", {"run_id": uid(11), "results": rows}) == \
        {"inserted": 2}
    assert ok(conn, "lab_judge_record_results", {"run_id": uid(11), "results": rows + [
        {**rows[0], "rubric_version": 2}]}) == {"inserted": 1}
    assert refusal(conn, "lab_judge_record_results", {"run_id": uid(11), "results": [
        {**rows[0], "sample_id": SAMPLES[2]}]}) == "invalid_request", "an unsent sample scored"
    over = ok(conn, "lab_judge_settle", {"run_id": uid(11), "actual": "40.00000001"})
    assert over["refused"] == "budget_exceeded" and over["run"]["state"] == "submitted", over
    assert audit(conn, uid(11)) == ["settle_refused"]
    done = ok(conn, "lab_judge_settle", {"run_id": uid(11), "actual": "12.50000000"})
    assert (done["state"], done["actual"], held(conn)) == \
        ("completed", "12.50000000", ("0.00000000", "12.50000000")), done
    assert ok(conn, "lab_judge_settle", {"run_id": uid(11), "actual": "30.00000000"}) == done
    assert held(conn)[1] == "12.50000000", "settled twice"
    ok(conn, "lab_judge_reserve", reserve(uid(12)))
    assert refusal(conn, "lab_judge_settle", {"run_id": uid(12), "actual": "1"}) == \
        "state_conflict", "an unsubmitted run settled"
    return "results deduplicated, unsent refused; overrun audited; settled once"


def _race(conn, n: int, work) -> list:
    gate, answers = threading.Barrier(n), [None] * n

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = work(mine, i)
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return answers


def check_concurrent_reservations_never_exceed_the_budget(conn) -> str:
    """JUDGE-BUDGET contention (commits): four runs of 40 reserved at once against a payer's
    budget of 100 - exactly two hold (80), two are `budget_exceeded`."""
    with conn.transaction():
        put_budget(conn, "100.00000000", PAYER_2)
    answers = _race(conn, 4, lambda c, i: call(c, "lab_judge_reserve", reserve(
        uid(0x20 + i), payer=PAYER_2))["state"])
    assert sorted(map(str, answers)) == ["budget_exceeded"] * 2 + ["prepared"] * 2, answers
    assert held(conn, PAYER_2)[0] == "80.00000000", held(conn, PAYER_2)
    return f"4 racing reservations of 40 under 100: {sorted(map(str, answers))}"


def check_a_duplicate_submit_under_contention_creates_one_intent(conn) -> str:
    """JUDGE-BUDGET duplicate submit (commits): four workers begin one prepared run at once -
    exactly one is told it created the intent (and may egress)."""
    with conn.transaction():
        put_budget(conn, "100.00000000", f"{PAYER_2[:-3]}fff")
        call(conn, "lab_judge_reserve", reserve(uid(0x30), payer=f"{PAYER_2[:-3]}fff"))
    answers = _race(conn, 4, lambda c, i: call(c, "lab_judge_begin_submit",
                                               {"run_id": uid(0x30)})["created"])
    assert sorted(answers) == [False, False, False, True], answers
    return "4 racing submitters: one created the intent"


@rolled_back
def check_runs_in_lists_this_providers_runs_by_state_oldest_first(conn) -> str:
    """WR-LSQ-C2A: the collect/reconcile passes need a listing of THIS provider's runs in a
    given state (never an unbounded scan, never another provider's), bounded to `limit`
    (ordered oldest-updated first in production; not asserted here, see 0048's note)."""
    ok(conn, "lab_judge_reserve", reserve(uid(0x50)))              # -> ambiguous, below
    ok(conn, "lab_judge_begin_submit", {"run_id": uid(0x50)})
    ok(conn, "lab_judge_record_sent", {"run_id": uid(0x50), "sample_ids": SAMPLES})
    ok(conn, "lab_judge_quarantine", {"run_id": uid(0x50), "reason": "no answer yet"})
    ok(conn, "lab_judge_reserve", reserve(uid(0x51)))              # -> prepared only
    with conn.transaction():
        call(conn, "lab_judge_reserve", reserve(uid(0x52), payer=OTHER_PAYER, provider=OTHER,
                                                grant=W["other_grant"]))
    assert refusal(conn, "lab_judge_runs_in", {"provider_org_id": NEMO, "states": [],
                                               "limit": 10}) == "invalid_request", \
        "every state at once is an unbounded scan, not a listing"
    # (Other checks in this module commit runs of their own that stay `prepared`, so
    # `prepared` is checked by membership; `ambiguous` is exclusively this check's.)
    ambiguous = ok(conn, "lab_judge_runs_in", {"provider_org_id": NEMO,
                                               "states": ["ambiguous"], "limit": 10})
    assert [r["run_id"] for r in ambiguous] == [uid(0x50)]
    prepared = [r["run_id"] for r in ok(conn, "lab_judge_runs_in", {
        "provider_org_id": NEMO, "states": ["prepared"], "limit": 10})]
    assert uid(0x51) in prepared and uid(0x50) not in prepared
    assert ok(conn, "lab_judge_runs_in", {"provider_org_id": OTHER, "states": ["ambiguous"],
                                          "limit": 10}) == [], \
        "OTHER's provider never sees NEMO's ambiguous run"
    limited = ok(conn, "lab_judge_runs_in", {"provider_org_id": NEMO,
                                             "states": ["ambiguous"], "limit": 0})
    assert limited == [], "limit 0 is bounded to nothing, not unbounded"
    return "own provider, by state, bounded, empty states refused"


@dataclass(frozen=True)
class _Scores:                       # J2's JudgeScores shape (accepted: it has scores)
    run_id: str
    sample_id: str
    rubric_version: int
    scores: tuple
    overall_pass: bool


def check_the_store_composes(conn) -> str:
    """`PgJudgeLedger` is J2's `JudgeLedger` over the RPCs: typed runs, (run, created), a
    result object stored whole, an overrun as `BudgetExceeded`, a foreign run `NotFound`."""
    with conn.transaction():
        put_budget(conn, "100.00000000", f"{PAYER_2[:-3]}eee")
    ledger = PgJudgeLedger(connector(pgharness.dsn(conn.info.dbname)))
    run_id = uid(0x40)

    async def go() -> list:
        got = [type(await ledger.db_now()).__name__]
        run = await ledger.reserve(run_id=run_id, provider_org_id=NEMO,
                                   payer_ref=f"{PAYER_2[:-3]}eee",
                                   consent=Consent(W["grant_id"], W["version"]),
                                   sample_ids=SAMPLES, media_ids=frozenset(SAMPLES[:1]),
                                   price_version="p1", max_cost=ProviderUsd("30"))
        got.append((run.state, str(run.reserved), run.consent.version == W["version"]))
        run, created = await ledger.begin_submit(run_id)
        got.append((run.submit_key, created))
        got.append((await ledger.record_sent(run_id, SAMPLES[:1])).sent_ids)
        got.append((await ledger.record_submission(run_id, "bx")).state)
        got.append(await ledger.record_results(run_id, [_Scores(run_id, SAMPLES[0], 1, (), True)]))
        try:
            await ledger.settle(run_id, ProviderUsd("31"))
        except errors.BudgetExceeded:
            got.append("budget_exceeded")
        got.append(str((await ledger.settle(run_id, ProviderUsd("2"))).actual))
        got.append(await ledger.run(uid(0x41)))
        try:
            await ledger.reserve(run_id=run_id, provider_org_id=OTHER, payer_ref=OTHER_PAYER,
                                 consent=Consent(*W["other_grant"]), sample_ids=SAMPLES,
                                 media_ids=frozenset(), price_version="p1",
                                 max_cost=ProviderUsd("1"))
        except errors.NotFound:
            got.append("not_found")
        return got
    got = asyncio.run(go())
    stored = conn.execute("select accepted, result->>'overall_pass' from "
                          "infrx.lab_judge_results where run_id = %s", (run_id,)).fetchall()
    assert got == ["datetime", ("prepared", "30.00000000", True), (f"submit:{run_id}", True),
                   (SAMPLES[0],), "submitted", 1, "budget_exceeded", "2.00000000", None,
                   "not_found"], got
    assert stored == [(True, "true")], stored
    return f"ledger round trip: {got[1:5]}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_the_ledger_is_off_without_its_flag,
    check_a_reservation_needs_the_current_external_judging_consent,
    check_the_budget_is_the_payers_one_provider_usd_cap,
    check_one_intent_then_the_samples_that_left_then_the_ack,
    check_a_revocation_between_consent_and_submit_stops_egress,
    check_an_ambiguous_submit_is_quarantined_never_resubmitted,
    check_results_are_stored_once_and_settled_once,
    check_concurrent_reservations_never_exceed_the_budget,
    check_a_duplicate_submit_under_contention_creates_one_intent,
    check_runs_in_lists_this_providers_runs_by_state_oldest_first, check_the_store_composes)}


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_d6j_judge(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
