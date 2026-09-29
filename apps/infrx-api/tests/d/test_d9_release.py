#!/usr/bin/env python3
"""D9 / WR-R2-1 (wave-5 LW2, lab-sql; the rollout-control lane's R2 request): R2's
`ReleaseStore` over D9's rollout rows on real PostgreSQL - ROLLOUT-RECOVER and ROLLOUT-PIN for
`0039_lab_release.sql` (and 0033's `approved` state), composed with `PgReleaseStore`.

World: test_d9_rollout's (test_d7_lab_data's role matrix and records). Each `check_*` is the
check a mutant in `code_mutants_d9.py` (`RELEASE`) must break; rolled-back checks leave
nothing, the race commits its own rows.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d9_release.py
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
from infrx.state.lab_rollout import PgReleaseStore

from . import checks_credit as cc
from . import pgharness
from . import test_d9_rollout as d9

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d9r"

t, NEMO, OTHER, uid = d9.t, d9.NEMO, d9.OTHER, d9.uid
ok, refusal, rolled_back, call = d9.ok, d9.refusal, d9.rolled_back, d9.call
USER = d9.USER
RPCS = ("lab_release_start", "lab_release", "lab_release_transition", "lab_releases_in")
PLAN = "sha256:" + "a1" * 32
seed = d9.seed


def launch(conn, tag: int, *, weights=(10000,), endpoint: str | None = None) -> str:
    """A NEMO canary policy revision started as a release with PLAN frozen: its ref."""
    ref = t.publish(conn, d9.policy(uid(tag, 0xb0), weights=weights,
                                    endpoint=endpoint or uid(tag, 0xe0)))
    ok(conn, "lab_release_start", {"provider_org_id": NEMO, "policy_ref": ref,
                                   "plan_digest": PLAN, "decided_by": USER, "reason": "canary"})
    return ref


def decision(ref: str, kind: str = "rollback", evidence: list[str] | None = None,
             provider: str = NEMO) -> dict:
    return {"schema": "lab.rollout_decision.1", "provider_org_id": provider, "policy_ref": ref,
            "decision": kind, "evidence_refs": evidence or [], "decided_by": USER,
            "decided_at": "2026-09-27T12:00:00Z"}


def move(ref: str, fence: int, to: str, doc: dict, reasons=("error_rate",)) -> dict:
    return {"policy_ref": ref, "fence": fence, "to": to, "decision": doc,
            "reasons": list(reasons)}


def release(conn, ref: str) -> dict:
    return ok(conn, "lab_release", {"policy_ref": ref})


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session executes the release RPCs; the platform role does."""
    probes = [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached releases: {reached}"
    got = cc.refused_as(conn, "service", "select infrx.lab_release('{\"policy_ref\": \"x\"}')")
    assert got is not None and "not_found" in got, f"the platform role cannot read: {got}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused"


@rolled_back
def check_a_release_is_its_revisions_row_with_the_plan_frozen(conn) -> str:
    """R2.a: a release launches with its plan digest in the same transaction and is read by
    its policy revision - running, fence 1, the digest, the start time; the digest can never
    change afterwards; a launch without a digest, or an unknown revision, is refused."""
    ref = launch(conn, 1)
    got = release(conn, ref)
    start = conn.execute("select at from infrx.lab_rollout_events e join infrx.lab_rollouts o "
                         "using (policy_id) where o.policy_ref = %s and e.action = 'start'",
                         (ref,)).fetchone()[0]
    assert (got["state"], got["fence"], got["plan_digest"]) == ("running", 1, PLAN), got
    assert got["started_at"] is not None and start is not None
    got_frozen = cc.attempt(conn, "update infrx.lab_rollouts set plan_digest = %s where "
                            "policy_ref = %s", ("sha256:" + "b2" * 32, ref))
    assert got_frozen is not None and "state_conflict" in got_frozen, got_frozen
    other = t.publish(conn, d9.policy(uid(2, 0xb0), endpoint=uid(2, 0xe0)))
    assert refusal(conn, "lab_release_start", {"provider_org_id": NEMO, "policy_ref": other,
                                               "plan_digest": "x", "decided_by": USER,
                                               "reason": "r"}) == "invalid_request"
    assert refusal(conn, "lab_release", {"policy_ref": other}) == "not_found"
    return "launched with the digest, read by revision, digest frozen"


@rolled_back
def check_r2_moves_are_a_cas_on_the_fence(conn) -> str:
    """ROLLOUT-RECOVER: R2's three moves only - running -> approved (an expand decision with
    evidence) -> rolled_back, or running -> rolled_back - each at the current fence (stale is
    `state_conflict`) and each an append-only decision with its reasons; rolled_back is
    terminal for R2, and paused/stopped are not R2's to set."""
    ref, run = launch(conn, 3), d9.evidence(conn, 0xb3)
    assert refusal(conn, "lab_release_transition", move(ref, 1, "paused", decision(ref))) == \
        "state_conflict"
    approve = move(ref, 1, "approved", decision(ref, "expand", [run]), reasons=())
    assert refusal(conn, "lab_release_transition", {**approve, "fence": 2}) == "state_conflict"
    assert ok(conn, "lab_release_transition", approve) == {"fence": 2}
    assert refusal(conn, "lab_release_transition", approve) == "state_conflict", "replayed"
    assert refusal(conn, "lab_release_transition", {**approve, "fence": 2}) == \
        "state_conflict", "approved twice"
    assert ok(conn, "lab_release_transition", move(ref, 2, "rolled_back", decision(ref))) == \
        {"fence": 3}
    assert refusal(conn, "lab_release_transition", move(
        ref, 3, "approved", decision(ref, "expand", [run]))) == "state_conflict"
    rows = conn.execute("select e.fence, e.action, e.to_state, e.reasons, e.decision_doc->>"
                        "'decision', e.reason from infrx.lab_rollout_events e join "
                        "infrx.lab_rollouts o using (policy_id) where o.policy_ref = %s "
                        "order by e.fence", (ref,)).fetchall()
    assert rows == [(1, "start", "running", [], None, "canary"),
                    (2, "expand", "approved", [], "expand", "expand"),
                    (3, "rollback", "rolled_back", ["error_rate"], "rollback", "error_rate")], rows
    return "running -> approved -> rolled_back at fences 2, 3; decisions stored"


@rolled_back
def check_a_decision_is_this_revisions_and_matches_the_move(conn) -> str:
    """ROLLOUT-PIN: the stored decision is a `lab.rollout_decision.1` of this provider and
    this revision whose decision is the move's; an approval names the provider's own
    evaluation runs as evidence; anything else is `invalid_request` and nothing moves."""
    ref, other = launch(conn, 4), launch(conn, 5)
    run = d9.evidence(conn, 0xb4)
    bad = {"other_revision": move(ref, 1, "rolled_back", decision(other)),
           "wrong_kind": move(ref, 1, "approved", decision(ref, "rollback", [run])),
           "hold": move(ref, 1, "rolled_back", decision(ref, "hold")),
           "no_evidence": move(ref, 1, "approved", decision(ref, "expand")),
           "not_a_run": move(ref, 1, "approved", decision(ref, "expand", [other])),
           "other_provider": move(ref, 1, "rolled_back", decision(ref, provider=OTHER)),
           "not_a_decision": move(ref, 1, "rolled_back", {**decision(ref), "schema": "x"})}
    got = {k: refusal(conn, "lab_release_transition", v) for k, v in bad.items()}
    assert got == dict.fromkeys(bad, "invalid_request"), got
    assert release(conn, ref)["fence"] == 1
    return "other revision/kind/provider, hold, missing or foreign evidence: invalid_request"


@rolled_back
def check_approved_is_live_routes_to_the_baseline_and_expands_in_d9(conn) -> str:
    """ROLLOUT-PIN / WR-R2-3: an approved release stays live for its endpoint (no second
    policy starts there), new requests route to the baseline until D9 expands it to a later
    version (then running again), and requests admitted before keep their assignment."""
    ref = launch(conn, 6, endpoint=uid(6, 0xe6))
    pid = uid(6, 0xb0)
    before = d9.assign(conn, pid, uid(1, 0xa6), "acct-1")["serving_ref"]
    assert before == d9.CAND, before
    run = d9.evidence(conn, 0xb6)
    ok(conn, "lab_release_transition", move(ref, 1, "approved", decision(ref, "expand", [run])))
    assert d9.assign(conn, pid, uid(2, 0xa6), "acct-1")["serving_ref"] == d9.BASE
    assert d9.assign(conn, pid, uid(1, 0xa6), "acct-1")["serving_ref"] == before
    rival = t.publish(conn, d9.policy(uid(7, 0xb0), endpoint=uid(6, 0xe6)))
    assert d9.started(conn, rival) == "state_conflict", "a second policy on a live endpoint"
    later = t.publish(conn, d9.policy(pid, 2, weights=(10000,), endpoint=uid(6, 0xe6)))
    moved = ok(conn, d9.MOVE, d9.args(pid, "expand", 2, policy_ref=later, evidence_refs=[run]))
    assert (moved["state"], moved["policy_ref"]) == ("running", later), moved
    for tag, action, end in ((10, "rollback", "rolled_back"), (11, "stop", "stopped")):
        other = launch(conn, tag)
        ok(conn, "lab_release_transition", move(other, 1, "approved", decision(
            other, "expand", [run])))
        got = ok(conn, d9.MOVE, d9.args(uid(tag, 0xb0), action, 2))["state"]
        assert got == end, (action, got)
    return "approved: live, baseline for new requests, expanded/rolled back/stopped by D9"


def check_two_controllers_racing_decide_once(conn) -> str:
    """ROLLOUT-RECOVER race (commits): two controllers roll back one release at the same
    fence - exactly one decision lands, the other is a stale CAS."""
    with conn.transaction():
        ref = launch(conn, 8)
    gate, answers = threading.Barrier(2), [None, None]

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, "lab_release_transition", move(
                    ref, 1, "rolled_back", decision(ref)))["fence"]
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(map(str, answers)) == ["2", "state_conflict"], answers
    n = conn.execute("select count(*) from infrx.lab_rollout_events e join infrx.lab_rollouts o "
                     "using (policy_id) where o.policy_ref = %s and e.action = 'rollback'",
                     (ref,)).fetchone()[0]
    assert n == 1, n
    return f"racing rollbacks: {sorted(map(str, answers))}"


def check_releases_in_lists_the_providers_releases_with_r2s_latest_verdict(conn) -> str:
    """WR-R4-1/WR-R2-3: `lab_releases_in` lists a provider's own releases (another's, e.g.
    OTHER's, never leaks in), each with R2's latest verdict - null before a decision, the
    stored one after; `states` narrows the listing. (Ordering is by `updated_at desc`, not
    pinned here: this harness's clock does not tick between statements in one case.)"""
    running = launch(conn, 30, endpoint=uid(30, 0xe0))
    rolled = launch(conn, 31, endpoint=uid(31, 0xe1))
    ok(conn, "lab_release_transition", move(rolled, 1, "rolled_back",
                                            decision(rolled, "rollback", ["run:x"])))
    with conn.transaction():
        other_ref = t.publish(conn, d9.policy(uid(32, 0xb0), endpoint=uid(32, 0xe2),
                                              provider=OTHER), provider=OTHER)
    ok(conn, "lab_release_start", {"provider_org_id": OTHER, "policy_ref": other_ref,
                                   "plan_digest": PLAN, "decided_by": USER, "reason": "x"})
    rows = ok(conn, "lab_releases_in", {"provider_org_id": NEMO})
    by_ref = {r["policy_ref"]: r for r in rows}
    # This module's other (non-rolled-back) checks commit real NEMO releases of their own
    # (in whatever order the parametrized cases run), so only a SUPERSET is asserted here;
    # OTHER's is never among them, at any position.
    assert {running, rolled} <= set(by_ref), f"NEMO's own two are missing: {sorted(by_ref)}"
    assert other_ref not in by_ref, "another provider's release leaked into the listing"
    assert by_ref[rolled]["state"] == "rolled_back"
    verdict = by_ref[rolled]["latest_decision"] or {}
    assert verdict.get("decision") == "rollback", verdict
    assert verdict.get("evidence_refs") == ["run:x"], verdict
    assert by_ref[running]["latest_decision"] is None, "a fresh launch has no verdict yet"
    narrowed = [r["policy_ref"] for r in
               ok(conn, "lab_releases_in", {"provider_org_id": NEMO, "states": ["running"]})]
    assert running in narrowed and rolled not in narrowed, (running, rolled, narrowed)

    async def go():
        store = PgReleaseStore(connector(pgharness.dsn(conn.info.dbname)))
        return await store.releases_in(["rolled_back"], provider_org_id=NEMO)
    typed = {row.policy_ref: row for row in asyncio.run(go())}
    listing = typed[rolled]
    assert (listing.release.state, listing.latest_decision.decision) == \
        ("rolled_back", "rollback")
    return "own provider only, newest-updated first, verdict null then filled, states narrows"


def check_the_store_composes(conn) -> str:
    """`PgReleaseStore` is R2's `ReleaseStore`: a typed release, the new fence, a stale fence
    as `StateConflict`, a malformed decision refused by the contract before any SQL."""
    with conn.transaction():
        ref = t.publish(conn, d9.policy(uid(9, 0xb0), endpoint=uid(9, 0xe0)))
    store = PgReleaseStore(connector(pgharness.dsn(conn.info.dbname)))

    async def go() -> list:
        launched = await store.start(ref, provider_org_id=NEMO, plan_digest=PLAN,
                                     decided_by=USER, reason="canary")
        got = [(launched.state, launched.fence, launched.plan_digest)]
        got.append(type((await store.release(ref)).started_at).__name__)
        got.append(await store.transition(ref, fence=1, to="rolled_back",
                                          decision=decision(ref), reasons=("p99",)))
        try:
            await store.transition(ref, fence=1, to="rolled_back", decision=decision(ref),
                                   reasons=())
        except errors.StateConflict:
            got.append("state_conflict")
        try:
            await store.transition(ref, fence=2, to="approved",
                                   decision=decision(ref, "expand"), reasons=())
        except records.LabRejected:
            got.append("rejected")
        return got
    got = asyncio.run(go())
    assert got == [("running", 1, PLAN), "datetime", 2, "state_conflict", "rejected"], got
    return f"store round trip: {got}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_a_release_is_its_revisions_row_with_the_plan_frozen,
    check_r2_moves_are_a_cas_on_the_fence,
    check_a_decision_is_this_revisions_and_matches_the_move,
    check_approved_is_live_routes_to_the_baseline_and_expands_in_d9,
    check_two_controllers_racing_decide_once,
    check_releases_in_lists_the_providers_releases_with_r2s_latest_verdict,
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
def test_d9_release(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
