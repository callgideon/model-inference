#!/usr/bin/env python3
"""D9 (wave-5 LW2, lab-sql; 13-lab-improvement-handoffs §D9): release policies and stable
experiment assignment on real PostgreSQL - ROLLOUT-PIN and ROLLOUT-RECOVER (the SQL half) for
`0033_lab_rollout.sql`, composed with `PgLabRolloutStore` and checked against F3's
`records.assign` (R162).

World: test_d7_lab_data's (NEMO and OTHER, with Lab data). Policies are published as 0029
records; each `check_*` is the check a mutant in `code_mutants_d9.py` must break; the
rolled-back ones leave nothing behind, the races commit their own rows.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d9_rollout.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_rollout import PgLabRolloutStore

from . import checks_credit as cc
from . import pgharness
from . import test_d7_lab_data as t

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d9"

NEMO, OTHER, uid = t.NEMO, t.OTHER, t.uid
ok, refusal, rolled_back, call = t.ok, t.refusal, t.rolled_back, t.call
TABLES = ("lab_rollouts", "lab_rollout_events", "lab_rollout_assignments")
RPCS = ("lab_rollout_start", "lab_rollout_transition", "lab_rollout_assign", "lab_rollout")
MOVE = "lab_rollout_transition"
USER = t.l2.DEV


def serving(n: int, provider: str = NEMO) -> str:
    return f"lab:serving:{provider}:{uid(n, 0x5e)}@sha256:{'e' * 64}"


BASE, CAND, CAND2 = serving(1), serving(2), serving(3)


def policy(policy_id: str, version: int = 1, *, weights=(2500,), mode: str = "canary",
           endpoint: str = uid(9, 0xe9), provider: str = NEMO) -> dict:
    cands = [{"serving_ref": s, "weight_bp": w} for s, w in zip((CAND, CAND2), weights)]
    if provider != NEMO:
        cands = [{**c, "serving_ref": c["serving_ref"].replace(NEMO, provider)} for c in cands]
    return {"schema": "lab.rollout_policy.1", "provider_org_id": provider,
            "policy_id": policy_id, "version": version, "created_at": "2026-09-27T10:00:00Z",
            "endpoint_id": endpoint, "baseline_ref": BASE.replace(NEMO, provider),
            "mode": mode, "cohort": "account", "candidates": cands}


def start(conn, ref: str, provider: str = NEMO) -> dict:
    return ok(conn, "lab_rollout_start", {"provider_org_id": provider, "policy_ref": ref,
                                          "decided_by": USER, "reason": "canary 25%"})


def started(conn, ref: str, provider: str = NEMO) -> str | None:
    return refusal(conn, "lab_rollout_start", {"provider_org_id": provider, "policy_ref": ref,
                                               "decided_by": USER, "reason": "x"})


def args(policy_id: str, action: str, fence: int, provider: str = NEMO, **kw) -> dict:
    return {"provider_org_id": provider, "policy_id": policy_id, "fence": fence,
            "action": action, "decided_by": USER, "reason": "review", **kw}


def assign(conn, policy_id: str, request_id: str, subject: str, provider: str = NEMO, **kw):
    return ok(conn, "lab_rollout_assign", {"provider_org_id": provider, "policy_id": policy_id,
                                           "request_id": request_id, "subject_key": subject,
                                           **kw})


def evidence(conn, tag: int) -> str:
    """A NEMO evaluation run record (published, not created)."""
    dataset = t.publish(conn, t.manifest(uid(1, tag), n=1, tag=tag))
    return t.publish(conn, t.eval_run(uid(2, tag), dataset, t.publish(conn, t.harness(uid(3, tag)))))


def seed(conn) -> None:
    t.seed(conn)


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads a rollout table or executes its RPCs; the platform
    role reads them and writes only through the RPCs; events and assignments are immutable."""
    probes = [f"select count(*) from infrx.{name}" for name in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached rollouts: {reached}"
    unread = [n for n in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(n)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [n for n in TABLES if not (cc.refused_as(conn, "service", f"delete from infrx.{n}")
                                         or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    unguarded = [n for n in TABLES if not conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"infrx.{n}",)
    ).fetchone()[0]]
    assert not unguarded, f"row security is off on {unguarded}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; service reads"


@rolled_back
def check_policies_are_validated_and_one_live_per_endpoint(conn) -> str:
    """ROLLOUT-RECOVER / D9.a: a rollout starts only from the caller's own policy version,
    within 10000 bp with candidate weight only in a canary; a policy starts once, and an
    endpoint has at most one live rollout until it is stopped."""
    for bad in ({**policy(uid(1, 0xf0), weights=(6000, 5000))},
                policy(uid(2, 0xf0), mode="shadow")):
        assert started(conn, t.publish(conn, bad)) == "invalid_request", bad["policy_id"]
    ref = t.publish(conn, policy(uid(3, 0xf0)))
    assert started(conn, ref, OTHER) == "not_found", "a provider ran another's policy"
    got = start(conn, ref)
    assert (got["state"], got["fence"], got["policy_ref"]) == ("running", 1, ref), got
    assert [(e["action"], e["to_state"], e["decided_by"]) for e in got["events"]] == \
        [("start", "running", USER)], got["events"]
    assert started(conn, ref) == "state_conflict", "a policy started twice"
    rival = t.publish(conn, policy(uid(4, 0xf0)))
    assert started(conn, rival) == "state_conflict", "two live rollouts on one endpoint"
    ok(conn, MOVE, args(uid(3, 0xf0), "stop", 1))
    assert start(conn, rival)["state"] == "running"
    return "validated weights; own policy; once per policy; one live per endpoint"


@rolled_back
def check_transitions_are_fenced_and_decisions_are_history(conn) -> str:
    """ROLLOUT-RECOVER: every move is a CAS on the fence (a stale publisher is
    `state_conflict`); pause is a hold, an expansion needs a later version of the same policy
    and the provider's evaluation runs as evidence, a rollback is a new decision; terminal
    rollouts do not move; the events are the full history and cannot be edited."""
    pid = uid(5, 0xf0)
    v1 = t.publish(conn, policy(pid))
    start(conn, v1)
    assert ok(conn, MOVE, args(pid, "pause", 1))["state"] == "paused"
    assert refusal(conn, MOVE, args(pid, "resume", 1)) == "state_conflict", "a stale fence"
    assert refusal(conn, MOVE, args(pid, "expand", 2)) == "state_conflict", "expanded paused"
    ok(conn, MOVE, args(pid, "resume", 2))
    v2 = t.publish(conn, policy(pid, 2, weights=(5000,)))
    run = evidence(conn, 0xf1)
    other_pid = t.publish(conn, policy(uid(6, 0xf0), 2))
    for kw, why in (({"policy_ref": v2}, "no evidence"),
                    ({"policy_ref": v2, "evidence_refs": [v1]}, "a policy as evidence"),
                    ({"policy_ref": v1, "evidence_refs": [run]}, "the same version"),
                    ({"policy_ref": other_pid, "evidence_refs": [run]}, "another policy")):
        assert refusal(conn, MOVE, args(pid, "expand", 3, **kw)) == "invalid_request", why
    got = ok(conn, MOVE, args(pid, "expand", 3, policy_ref=v2, evidence_refs=[run]))
    assert (got["state"], got["policy_ref"], got["fence"]) == ("running", v2, 4), got
    got = ok(conn, MOVE, args(pid, "rollback", 4))
    assert got["state"] == "rolled_back"
    assert refusal(conn, MOVE, args(pid, "resume", 5)) == "state_conflict", "a terminal moved"
    assert [(e["fence"], e["action"], e["from_state"], e["to_state"], e["decision"],
             e["evidence_refs"]) for e in got["events"]] == [
        (1, "start", None, "running", None, []), (2, "pause", "running", "paused", "hold", []),
        (3, "resume", "paused", "running", None, []),
        (4, "expand", "running", "running", "expand", [run]),
        (5, "rollback", "running", "rolled_back", "rollback", [])], got["events"]
    for sql in ("update infrx.lab_rollout_events set decision = 'hold'",
                "delete from infrx.lab_rollout_events"):
        refused = cc.attempt(conn, sql)
        assert refused is not None and refused.startswith("23514"), f"{sql}: {refused}"
    return "fenced CAS; hold/expand/rollback decisions with evidence; immutable history"


@rolled_back
def check_assignment_is_stable_and_is_the_contracts_cohort(conn) -> str:
    """ROLLOUT-PIN: an assignment is F3's `records.assign` (R162, the same bucket and cohort
    digest) and is stored once - a retry, even after an expansion or a rollback, is the same
    answer; new requests follow the version in force, and the baseline once rolled back; the
    subject itself is never stored."""
    pid = uid(7, 0xf0)
    v1_payload = policy(pid, weights=(2500, 2500))
    v1 = t.publish(conn, v1_payload)
    start(conn, v1)
    contract = records.RolloutPolicy.model_validate(v1_payload)
    subjects = [f"acct-{i}" for i in range(200)]
    first = {}
    for i, subject in enumerate(subjects):
        got = assign(conn, pid, uid(i, 0xa5), subject)
        want = records.assign(contract, v1, subject, uid(i, 0xa5))
        assert (got["serving_ref"], got["cohort_digest"], got["pinned_by"]) == \
            (want.serving_ref, want.cohort_digest, "cohort"), (subject, got, want)
        first[subject] = got
    spread = {s: sum(1 for g in first.values() if g["serving_ref"] == s)
              for s in (BASE, CAND, CAND2)}
    assert all(spread.values()), spread
    v2 = t.publish(conn, policy(pid, 2, weights=(5000, 5000)))
    ok(conn, MOVE, args(pid, "expand", 1, policy_ref=v2, evidence_refs=[evidence(conn, 0xf2)]))
    assert assign(conn, pid, uid(0, 0xa5), subjects[0]) == first[subjects[0]], "a retry moved"
    fresh = assign(conn, pid, uid(900, 0xa5), subjects[0])
    assert fresh["policy_ref"] == v2 and fresh["serving_ref"] in (CAND, CAND2), fresh
    ok(conn, MOVE, args(pid, "rollback", 2))
    after = [assign(conn, pid, uid(1000 + i, 0xa5), s)["serving_ref"]
             for i, s in enumerate(subjects[:20])]
    assert set(after) == {BASE}, f"a rolled-back rollout still routes to {set(after)}"
    assert assign(conn, pid, uid(900, 0xa5), subjects[0]) == fresh, "rollback re-routed"
    leaked = conn.execute("select count(*) from infrx.lab_rollout_assignments a where "
                          "a::text like '%%acct-%%'").fetchone()[0]
    assert leaked == 0, "a subject was stored"
    return f"200 assignments == records.assign (spread {sorted(spread.values())}); stable"


@rolled_back
def check_explicit_pins_are_honoured(conn) -> str:
    """ROLLOUT-PIN: an explicit pin to one of the provider's serving revisions is honoured
    as given - whatever the bucket, even once stopped - and recorded as explicit; another
    provider's serving ref, or another kind, is `not_found`."""
    pid = uid(8, 0xf0)
    start(conn, t.publish(conn, policy(pid, weights=(10000,))))
    pinned = assign(conn, pid, uid(1, 0xa6), "acct-1", explicit_serving_ref=BASE)
    assert (pinned["serving_ref"], pinned["pinned_by"]) == (BASE, "explicit"), pinned
    assert assign(conn, pid, uid(2, 0xa6), "acct-1")["serving_ref"] == CAND, "100% canary"
    ok(conn, MOVE, args(pid, "stop", 1))
    assert assign(conn, pid, uid(3, 0xa6), "acct-1", explicit_serving_ref=CAND2)[
        "serving_ref"] == CAND2
    for bad in (serving(4, OTHER), t.EVALUATOR):
        assert refusal(conn, "lab_rollout_assign", {
            "provider_org_id": NEMO, "policy_id": pid, "request_id": uid(4, 0xa6),
            "subject_key": "acct-1", "explicit_serving_ref": bad}) == "not_found", bad
    return "explicit pins honoured (running and stopped); foreign pins not_found"


@rolled_back
def check_other_providers_reach_nothing(conn) -> str:
    """LAB-ACCESS: another provider cannot move, assign under or read NEMO's rollout."""
    pid = uid(9, 0xf0)
    start(conn, t.publish(conn, policy(pid)))
    got = {"move": refusal(conn, MOVE, args(pid, "pause", 1, OTHER)),
           "assign": refusal(conn, "lab_rollout_assign", {
               "provider_org_id": OTHER, "policy_id": pid, "request_id": uid(1, 0xa7),
               "subject_key": "x"}),
           "read": refusal(conn, "lab_rollout", {"provider_org_id": OTHER, "policy_id": pid})}
    assert got == dict.fromkeys(got, "not_found"), got
    return "move, assign and read are not_found"


def _race(conn, calls: list[tuple[str, dict]]) -> list:
    gate, answers = threading.Barrier(len(calls)), [None] * len(calls)

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, *calls[i])
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return answers


def check_concurrent_moves_make_one_decision(conn) -> str:
    """ROLLOUT-RECOVER races (commit their own rows): four publishers at one fence - exactly
    one decision lands; two policies started on one endpoint at once - exactly one runs."""
    pid = uid(10, 0xf0)
    with conn.transaction():
        start(conn, t.publish(conn, policy(pid, endpoint=uid(10, 0xe9))))
    moves = _race(conn, [(MOVE, args(pid, action, 1))
                         for action in ("rollback", "pause", "stop", "rollback")])
    won = [m for m in moves if isinstance(m, dict)]
    assert len(won) == 1 and moves.count("state_conflict") == 3, moves
    events = ok(conn, "lab_rollout", {"provider_org_id": NEMO, "policy_id": pid})["events"]
    assert len(events) == 2, events
    with conn.transaction():
        refs = [t.publish(conn, policy(uid(11 + i, 0xf0), endpoint=uid(11, 0xe9)))
                for i in range(2)]
    starts = _race(conn, [("lab_rollout_start", {"provider_org_id": NEMO, "policy_ref": r,
                                                 "decided_by": USER, "reason": "go"})
                          for r in refs])
    assert sorted(s if isinstance(s, str) else s["state"] for s in starts) == \
        ["running", "state_conflict"], starts
    return f"fenced race: 1 of 4 decided; endpoint race: {sorted(map(str, starts))[:1]}"


def check_repeat_assignment_under_contention(conn) -> str:
    """ROLLOUT-PIN: four retries of one request at once get one identical answer, one row
    (commits its own rows)."""
    pid = uid(20, 0xf0)
    with conn.transaction():
        start(conn, t.publish(conn, policy(pid, endpoint=uid(20, 0xe9), weights=(5000,))))
    answers = _race(conn, [("lab_rollout_assign", {
        "provider_org_id": NEMO, "policy_id": pid, "request_id": uid(1, 0xa8),
        "subject_key": f"acct-{i}"}) for i in range(4)])
    assert all(a == answers[0] for a in answers) and isinstance(answers[0], dict), answers
    rows = conn.execute("select count(*) from infrx.lab_rollout_assignments where "
                        "policy_id = %s", (pid,)).fetchone()[0]
    assert rows == 1, rows
    return "4 racing retries: one answer, one row"


def check_the_store_composes(conn) -> str:
    """`PgLabRolloutStore` over the RPCs: start, fenced moves (typed stale refusal), assign."""
    pid = uid(30, 0xf0)
    with conn.transaction():
        ref = t.publish(conn, policy(pid, endpoint=uid(30, 0xe9)))
    store = PgLabRolloutStore(connector(pgharness.dsn(conn.info.dbname)))

    async def go() -> list:
        got = [(await store.start(ref, provider_org_id=NEMO, decided_by=USER,
                                  reason="go"))["fence"]]
        got.append((await store.transition(pid, "pause", fence=1, provider_org_id=NEMO,
                                           decided_by=USER, reason="hold"))["state"])
        try:
            await store.transition(pid, "resume", fence=1, provider_org_id=NEMO,
                                   decided_by=USER, reason="stale")
        except errors.StateConflict:
            got.append("stale")
        got.append((await store.assign(pid, uid(1, 0xa9), "acct-1", provider_org_id=NEMO,
                                       explicit_serving_ref=CAND2))["serving_ref"])
        got.append((await store.rollout(pid, provider_org_id=NEMO))["fence"])
        return got
    got = asyncio.run(go())
    assert got == [1, "paused", "stale", CAND2, 2], got
    return f"store round trip: {got}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_policies_are_validated_and_one_live_per_endpoint,
    check_transitions_are_fenced_and_decisions_are_history,
    check_assignment_is_stable_and_is_the_contracts_cohort, check_explicit_pins_are_honoured,
    check_other_providers_reach_nothing, check_concurrent_moves_make_one_decision,
    check_repeat_assignment_under_contention, check_the_store_composes)}


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
def test_d9(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
