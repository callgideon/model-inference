#!/usr/bin/env python3
"""D6J (wave-5 LW2, lab-sql; 09-amendment-workstreams §D6J): Lab consent snapshots, PROVIDER_USD
budgets with a named payer and the external submission protocol on real PostgreSQL -
JUDGE-BUDGET and LAB-ACCESS (the SQL half) for `0031_lab_consent.sql`, composed with
`PgLabConsentStore`.

World: test_d7_lab_data's (the L2 role matrix, C1's provider_sharing + training grant to NEMO
with one source under it) plus the `lab_submission` flag on and a NEMO payer budget of 100.
Each `check_*` is the check a mutant in `code_mutants_d6j.py` must break; the rolled-back ones
leave nothing behind, the two contention drills commit their own objects.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d6j_consent.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import states
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_consent import PgLabConsentStore

from . import checks_credit as cc
from . import pgharness
from . import test_d7_lab_data as t

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d6j"

NEMO, OTHER, C1, l2 = t.NEMO, t.OTHER, t.C1, t.l2
call, ok, refusal, rolled_back, uid = t.call, t.ok, t.refusal, t.rolled_back, t.uid
PAYER = t.PAYER
TABLES = ("lab_budgets", "lab_budget_limits", "lab_submissions", "lab_submission_consents")
RPCS = ("lab_put_budget", "lab_budget", "lab_prepare_submission", "lab_submission_transition",
        "lab_submission")
MOVE = "lab_submission_transition"


def payer(n: int) -> str:
    return f"lab:payer:{NEMO}:{uid(n, 0x9a)}@sha256:{'b' * 64}"


def xrun(conn, tag: int, *, purpose: str = "training", limit: str = "40.00000000",
         payer_ref: str = PAYER, unit: str = "PROVIDER_USD", dataset: str | None = None,
         provider: str = NEMO, run_id: str | None = None) -> tuple[str, str]:
    """A published external run over a (new) NEMO dataset: (its ref, its id)."""
    dataset = dataset or t.publish(conn, t.manifest(uid(1, tag), n=2, tag=tag))
    run_id = run_id or uid(2, tag)
    payload = {**t.external_run(run_id, dataset), "purpose": purpose, "provider_org_id": provider,
               "budget": {"limit": {"unit": unit, "value": limit},
                          "reserved": {"unit": unit, "value": "0.00000000"},
                          "payer_ref": payer_ref}}
    return t.publish(conn, payload, provider), run_id


def prepare(conn, ref: str, provider: str = NEMO):
    return ok(conn, "lab_prepare_submission", {"provider_org_id": provider,
                                               "external_run_ref": ref, "actor": "dev@nemo"})


def move(conn, run_id: str, state: str, provider: str = NEMO, **fields) -> dict:
    return ok(conn, MOVE, {"provider_org_id": provider, "external_run_id": run_id,
                           "state": state, **fields})


def moved(conn, run_id: str, state: str, provider: str = NEMO, **fields) -> str | None:
    return refusal(conn, MOVE, {"provider_org_id": provider, "external_run_id": run_id,
                                "state": state, **fields})


def budget(conn, payer_ref: str = PAYER) -> tuple[str, str, str]:
    b = ok(conn, "lab_budget", {"provider_org_id": NEMO, "payer_ref": payer_ref})
    return b["limit"], b["reserved"], b["settled"]


def usd(value: str) -> dict:
    return {"unit": "PROVIDER_USD", "value": value}


def put_budget(conn, limit: str, payer_ref: str = PAYER, provider: str = NEMO):
    return ok(conn, "lab_put_budget", {"provider_org_id": provider, "payer_ref": payer_ref,
                                       "limit": limit, "actor": "ops@nemo", "reason": "q4"})


def seed(conn) -> None:
    t.seed(conn)
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('lab_submission', true, 'd6j-test', 'test')")
    put_budget(conn, "100.00000000")


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads or writes a D6J table or executes its RPCs; the
    platform role reads the tables and writes them only through the RPCs."""
    probes = [f"select count(*) from infrx.{name}" for name in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached D6J state: {reached}"
    unread = [name for name in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(name)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [name for name in TABLES if not (cc.refused_as(
        conn, "service", f"delete from infrx.{name}") or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    unguarded = [name for name in TABLES if not conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"infrx.{name}",)
    ).fetchone()[0]]
    assert not unguarded, f"row security is off on {unguarded}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; service reads"


@rolled_back
def check_submission_has_its_own_flag(conn) -> str:
    """09 §D6J: submission is enabled by `lab_submission` alone - off (or missing) refuses
    preparing and every move, whatever `feedback` says; `feedback` on does not enable it."""
    ref, run_id = xrun(conn, 0xc0)
    prepare(conn, ref)
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('feedback', true, 't', 't') on conflict (name) do update set enabled = true")
    other, _ = xrun(conn, 0xc1)
    for enabled in (False, None):
        if enabled is None:
            conn.execute("delete from infrx.feature_flags where name = 'lab_submission'")
        else:
            cc.set_flag(conn, "lab_submission", enabled)
        got = (refusal(conn, "lab_prepare_submission", {"provider_org_id": NEMO,
                                                        "external_run_ref": other, "actor": "d"}),
               moved(conn, run_id, "submitting"))
        assert got == ("dependency_unavailable",) * 2, f"flag {enabled}: {got}"
    return "off and missing both refuse prepare and moves; feedback does not enable them"


@rolled_back
def check_budget_is_provider_usd_under_a_named_payer(conn) -> str:
    """R159: a budget is PROVIDER_USD under a `lab:payer` ref of the caller's own provider;
    every limit is recorded with its actor and reason; a limit below what is held and spent
    is `budget_exceeded`; a run whose payer has no budget, or whose budget is not
    PROVIDER_USD, is not prepared."""
    assert refusal(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": PAYER.replace(
        NEMO, OTHER), "limit": "5", "actor": "a", "reason": "r"}) == "not_found"
    for bad in (PAYER.split("@")[0] + "@latest", PAYER.replace("lab:payer:", "lab:serving:")):
        assert refusal(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": bad,
                                                "limit": "5", "actor": "a", "reason": "r"}) \
            == "invalid_request", bad
    assert refusal(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": PAYER,
                                            "limit": "-1", "actor": "a", "reason": "r"}) \
        == "invalid_request"
    ref, _ = xrun(conn, 0xc2)
    prepare(conn, ref)
    assert budget(conn) == ("100.00000000", "40.00000000", "0.00000000"), budget(conn)
    assert refusal(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": PAYER,
                                            "limit": "39.99999999", "actor": "a",
                                            "reason": "r"}) == "budget_exceeded"
    put_budget(conn, "40.00000000")
    history = conn.execute("select version, limit_value::text, actor from infrx.lab_budget_limits "
                           "where payer_ref = %s order by version", (PAYER,)).fetchall()
    assert history == [(1, "100.00000000", "ops@nemo"), (2, "40.00000000", "ops@nemo")], history
    unfunded, _ = xrun(conn, 0xc3, payer_ref=payer(1))
    assert refusal(conn, "lab_prepare_submission", {"provider_org_id": NEMO,
                                                    "external_run_ref": unfunded,
                                                    "actor": "d"}) == "not_found"
    credit, _ = xrun(conn, 0xc4, unit="CREDIT")
    assert refusal(conn, "lab_prepare_submission", {"provider_org_id": NEMO,
                                                    "external_run_ref": credit,
                                                    "actor": "d"}) == "invalid_request"
    return "own payer only; limits audited; no limit under held + spent; USD only"


@rolled_back
def check_consent_is_snapshotted_and_current_before_egress(conn) -> str:
    """LAB-ACCESS / JUDGE-BUDGET: preparing needs every grant of the dataset current for the
    run's OWN purpose and snapshots it at its version; the intent needs that snapshot to be
    still current - a revocation, and even a re-grant since (a new version), refuse it with
    `consent_missing`, leaving the run prepared with no intent; cancelling releases the
    reservation."""
    judging, _ = xrun(conn, 0xc5, purpose="external_judging")
    assert refusal(conn, "lab_prepare_submission", {"provider_org_id": NEMO,
                                                    "external_run_ref": judging,
                                                    "actor": "d"}) == "consent_missing"
    ref, run_id = xrun(conn, 0xc6)
    got = prepare(conn, ref)
    version = conn.execute("select max(version) from infrx.lab_access_grants where grant_id = %s",
                           (t.W["grant_id"],)).fetchone()[0]
    assert got["consents"] == [{"grant_id": t.W["grant_id"], "grant_version": version}], got
    assert (got["state"], got["intent_id"], got["submit_key"]) == \
        ("prepared", None, f"submit:{run_id}"), got
    grantor = {"actor_user_id": C1, "grantor_org_id": l2.org(conn, C1),
               "recipient_provider_org_id": NEMO}
    ok(conn, "lab_revoke_access_grant", grantor)
    assert prepare(conn, ref) == got, "a replay after revocation is not the same submission"
    assert moved(conn, run_id, "submitting") == "consent_missing", "revoked content left"
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["provider_sharing", "training"]))
    assert moved(conn, run_id, "submitting") == "consent_missing", \
        "a consent snapshot outlived the version it was taken at"
    got = ok(conn, "lab_submission", {"provider_org_id": NEMO, "external_run_id": run_id})
    assert (got["state"], got["intent_id"]) == ("prepared", None), got
    assert moved(conn, run_id, "cancelled", cost=usd("1.00000000")) == "invalid_request", \
        "work that never left was charged"
    move(conn, run_id, "cancelled")
    assert budget(conn)[1:] == ("0.00000000", "0.00000000"), budget(conn)
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["training"],
                                              expires_at="2099-01-01T00:00:00Z"))
    expiring, expiring_id = xrun(conn, 0xcf)
    prepare(conn, expiring)
    conn.execute("update infrx_test.clock set offset_s = offset_s + "
                 "('2099-01-02'::timestamptz - infrx.now())")
    assert moved(conn, expiring_id, "submitting") == "consent_missing", "expired content left"
    return "own-purpose gate at prepare; snapshot version and currency at the intent"


@rolled_back
def check_reserve_intent_ack_and_settle(conn) -> str:
    """JUDGE-BUDGET: prepare reserves the run's limit once (a replay reserves nothing); the
    intent is one id before egress; the ack names the batch (a retried ack replays, another
    batch conflicts); completion settles a cost within the reservation and releases the rest;
    undeclared moves are `state_conflict`."""
    ref, run_id = xrun(conn, 0xc7)
    first = prepare(conn, ref)
    assert prepare(conn, ref) == first and budget(conn)[1] == "40.00000000", budget(conn)
    assert moved(conn, run_id, "submitted", external_batch_id="b1") == "state_conflict"
    intent = move(conn, run_id, "submitting")
    assert intent["state"] == "submitting" and intent["intent_id"], intent
    assert moved(conn, run_id, "submitted") == "invalid_request", "an ack without its batch"
    acked = move(conn, run_id, "submitted", external_batch_id="b1")
    assert move(conn, run_id, "submitted", external_batch_id="b1") == acked
    assert acked["intent_id"] == intent["intent_id"], "the intent was rewritten"
    assert moved(conn, run_id, "submitted", external_batch_id="b2") == "idempotency_conflict"
    assert moved(conn, run_id, "completed") == "invalid_request", "completed without its cost"
    assert moved(conn, run_id, "completed", cost=usd("40.00000001")) == "budget_exceeded"
    assert moved(conn, run_id, "completed", cost={"unit": "CREDIT", "value": "1.00000000"}) \
        == "invalid_request", "a CREDIT cost settled against a USD budget"
    done = move(conn, run_id, "completed", cost=usd("12.50000000"))
    assert (done["state"], done["settled"]) == ("completed", "12.50000000"), done
    assert budget(conn) == ("100.00000000", "0.00000000", "12.50000000"), budget(conn)
    assert move(conn, run_id, "completed", cost=usd("12.50000000")) == done
    assert moved(conn, run_id, "completed", cost=usd("1.00000000")) == "idempotency_conflict"
    assert budget(conn)[2] == "12.50000000", "a replayed settlement settled twice"
    assert moved(conn, run_id, "failed") == "state_conflict"
    forced = cc.attempt(conn, "update infrx.lab_submissions set state = 'submitting' "
                              "where external_run_id = %s", (run_id,))
    assert forced is not None and "state_conflict" in forced, forced
    return "reserve once; one intent; ack replay/conflict; settle within; guarded moves"


@rolled_back
def check_ambiguous_is_quarantined_never_resubmitted(conn) -> str:
    """JUDGE-BUDGET unknown submit timeout: an ambiguous outcome is quarantined with its
    reason and can never be submitted again - only reconciled by lookup, to `submitted` (its
    batch) or to `failed`, which releases the whole reservation."""
    ref, run_id = xrun(conn, 0xc8)
    prepare(conn, ref)
    move(conn, run_id, "submitting")
    assert moved(conn, run_id, "ambiguous") == "invalid_request", "a quarantine without reason"
    held = move(conn, run_id, "ambiguous", reason="connector timeout after 30 s")
    assert held["quarantine_reason"] == "connector timeout after 30 s", held
    assert moved(conn, run_id, "submitting") == "ambiguous_submission", "an ambiguous resubmit"
    assert move(conn, run_id, "submitted", external_batch_id="found-by-lookup")["state"] == \
        "submitted"
    other, other_id = xrun(conn, 0xc9)
    prepare(conn, other)
    move(conn, other_id, "submitting")
    move(conn, other_id, "ambiguous", reason="timeout")
    failed = move(conn, other_id, "failed")
    assert (failed["state"], failed["settled"]) == ("failed", "0.00000000"), failed
    assert budget(conn)[1:] == ("40.00000000", "0.00000000"), budget(conn)
    return "quarantined with reason; no resubmit; reconciled to submitted or failed (released)"


@rolled_back
def check_other_providers_reach_nothing(conn) -> str:
    """LAB-ACCESS: another provider cannot prepare, move or read NEMO's run, spend NEMO's
    payer, nor - publishing its own record under NEMO's external run id - reach NEMO's
    submission; each is `not_found`, as an unknown one."""
    ref, run_id = xrun(conn, 0xca)
    prepare(conn, ref)
    got = {
        "prepare": refusal(conn, "lab_prepare_submission", {"provider_org_id": OTHER,
                                                            "external_run_ref": ref,
                                                            "actor": "x"}),
        "move": moved(conn, run_id, "submitting", provider=OTHER),
        "read": refusal(conn, "lab_submission", {"provider_org_id": OTHER,
                                                 "external_run_id": run_id}),
        "budget": refusal(conn, "lab_budget", {"provider_org_id": OTHER, "payer_ref": PAYER}),
    }
    other_payer = f"lab:payer:{OTHER}:{uid(3, 0x9a)}@sha256:{'c' * 64}"
    put_budget(conn, "100.00000000", other_payer, OTHER)
    other_ds = t.publish(conn, t.manifest(uid(1, 0xcb), n=1, provider=OTHER, tag=0xcb,
                                          source=t.W["other_source"], grant=t.W["other_grant"]),
                         OTHER)
    ok(conn, "lab_put_access_grant", l2.scope(conn, owner=l2.BOTH, provider=OTHER,
                                              model_ids=[l2.OTHER_MODEL],
                                              purposes=["provider_sharing", "training"]))
    squat, _ = xrun(conn, 0xcb, dataset=other_ds, payer_ref=other_payer, provider=OTHER,
                    run_id=run_id)
    got["squat"] = refusal(conn, "lab_prepare_submission", {"provider_org_id": OTHER,
                                                            "external_run_ref": squat,
                                                            "actor": "x"})
    assert got == dict.fromkeys(got, "not_found"), got
    assert ok(conn, "lab_submission", {"provider_org_id": NEMO, "external_run_id": run_id}
              )["external_run_ref"] == ref
    return "prepare, move, read, budget and an id squat are all not_found"


def _race(conn, n: int, work) -> list:
    """`work(connection)` from `n` connections released together; each answer or error code."""
    gate, answers = threading.Barrier(n), [None] * n

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = work(mine)
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return answers


def check_concurrent_reservations_never_exceed_the_limit(conn) -> str:
    """JUDGE-BUDGET contention: four runs of 40 prepared at once against a budget of 100 -
    exactly two hold (80), the others are `budget_exceeded` (commits its own objects)."""
    with conn.transaction():
        put_budget(conn, "100.00000000", payer(2))
        refs = [xrun(conn, 0xd0 + i, payer_ref=payer(2))[0] for i in range(4)]
    answers = _race(conn, 4, lambda c: call(c, "lab_prepare_submission", {
        "provider_org_id": NEMO, "external_run_ref": refs.pop(), "actor": "d"})["state"])
    assert sorted(map(str, answers)) == ["budget_exceeded"] * 2 + ["prepared"] * 2, answers
    assert budget(conn, payer(2))[1] == "80.00000000", budget(conn, payer(2))
    return f"4 racing reservations of 40 under 100: {sorted(map(str, answers))}"


def check_one_submit_intent_under_contention(conn) -> str:
    """JUDGE-BUDGET duplicate submit: four submitters of one prepared run at once - exactly
    one intent; the others are `ambiguous_submission` (commits its own objects)."""
    with conn.transaction():
        ref, run_id = xrun(conn, 0xd8)
        prepare(conn, ref)
    answers = _race(conn, 4, lambda c: call(c, MOVE, {
        "provider_org_id": NEMO, "external_run_id": run_id, "state": "submitting"})["intent_id"])
    intents = [a for a in answers if a != "ambiguous_submission"]
    assert len(intents) == 1 and answers.count("ambiguous_submission") == 3, answers
    return "4 racing submitters: one intent, three ambiguous_submission"


@rolled_back
def check_transitions_are_the_contracts(conn) -> str:
    """R161: the SQL move table is exactly `states.TRANSITIONS['external_run']`, pair by pair."""
    table = states.TRANSITIONS["external_run"]
    names = states.STATES["external_run"]
    wrong = [(a, b) for a in names for b in names if conn.execute(
        "select infrx.lab_submission_may(%s, %s)", (a, b)).fetchone()[0]
        != (b in table.get(a, ()))]
    assert not wrong, f"SQL and contract disagree on {wrong}"
    return f"{len(names) ** 2} pairs agree"


def check_the_store_composes(conn) -> str:
    """`PgLabConsentStore` over the RPCs: the protocol end to end and typed refusals."""
    with conn.transaction():
        ref, run_id = xrun(conn, 0xd9)
    store = PgLabConsentStore(connector(pgharness.dsn(conn.info.dbname)))

    async def go() -> list:
        got = [(await store.prepare(ref, provider_org_id=NEMO, actor="d"))["state"]]
        got.append((await store.begin_submit(run_id, provider_org_id=NEMO))["state"])
        try:
            await store.begin_submit(run_id, provider_org_id=NEMO)
        except errors.AmbiguousSubmission:
            got.append("ambiguous_submission")
        got.append((await store.ambiguous(run_id, "timeout", provider_org_id=NEMO))["state"])
        got.append((await store.accepted(run_id, "b9", provider_org_id=NEMO))["state"])
        got.append((await store.finish(run_id, "completed", provider_org_id=NEMO,
                                       cost=usd("3.00000000")))["settled"])
        got.append((await store.submission(run_id, provider_org_id=NEMO))["state"])
        got.append((await store.budget(PAYER, provider_org_id=NEMO))["settled"])
        return got
    got = asyncio.run(go())
    assert got == ["prepared", "submitting", "ambiguous_submission", "ambiguous", "submitted",
                   "3.00000000", "completed", "3.00000000"], got
    return f"store round trip: {got}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_submission_has_its_own_flag,
    check_budget_is_provider_usd_under_a_named_payer,
    check_consent_is_snapshotted_and_current_before_egress, check_reserve_intent_ack_and_settle,
    check_ambiguous_is_quarantined_never_resubmitted, check_other_providers_reach_nothing,
    check_concurrent_reservations_never_exceed_the_limit,
    check_one_submit_intent_under_contention, check_transitions_are_the_contracts,
    check_the_store_composes)}


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
def test_d6j(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
