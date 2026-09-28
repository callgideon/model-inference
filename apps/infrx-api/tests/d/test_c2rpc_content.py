#!/usr/bin/env python3
"""C2-RPC (wave-5 LW3, lab-sql; the content lane's WR-C2-1): the content-access RPCs of
`0041_lab_content_refs.sql` on real PostgreSQL - LAB-ACCESS, DATA-RIGHTS and DUR-RLS for trace
content refs (issue / redeem / held and the session door) and N3's sample tombstones and
content bounds, composed with `PgContentRefs` and `PgSampleRestrictions`.

The executable contract is the content lane's `infrx/content/fakes.py` FakeContentRefs; each
check below asserts one of its answers on the RPC. World: test_d7_lab_data's role matrix (two
consumers C1 and BOTH, providers NEMO - DEV developer, ADMIN administrator, VIEWER viewer - and
OTHER - BOTH developer -, BOTH in both products) plus C1's current grant to NEMO over request
and response content (provider_sharing, training; 30 days) and one admitted job of C1's and
one of BOTH's. Each `check_*` is the check a mutant in `code_mutants_c2rpc.py` must break.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_c2rpc_content.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_content import PgContentRefs, PgSampleRestrictions
from infrx.state.lab_data import grant_ref

from . import checks
from . import checks_credit as cc
from . import pgharness
from . import test_d7_lab_data as t
from .test_c2rpc_units import ensure_ref_binding

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_c2rpc"

l2, NEMO, OTHER, C1, BOTH = t.l2, t.NEMO, t.OTHER, t.C1, t.BOTH
DEV, ADMIN, VIEWER = t.l2.DEV, t.l2.ADMIN, t.l2.VIEWER
call, ok, refusal, rolled_back, uid = t.call, t.ok, t.refusal, t.rolled_back, t.uid
TABLES = ("lab_content_refs", "lab_sample_tombstones", "lab_sample_bounds")
RPCS = ("lab_content_ref_issue", "lab_content_ref_redeem", "lab_content_ref_held",
        "lab_tombstone_samples", "lab_bound_samples", "lab_blocked_samples",
        "lab_permitted_samples")
DOOR = "public.lab_content_ref_issue(text, uuid, text, uuid, text)"
CATS = ("request_content", "response_content")
JOB, JOB_BOTH = uid(1, 0xc2), uid(2, 0xc2)
W: dict[str, str] = {}


def digest(n: int) -> str:
    return f"{n:064x}"


def content_grant(conn, **over) -> dict:
    """C1's grant to NEMO over both content categories (the next version)."""
    return ok(conn, "lab_put_access_grant", l2.scope(
        conn, **{"categories": list(CATS), "purposes": ["provider_sharing", "training"],
                 **over}))


def issue(n: int = 1, *, user: str = DEV, provider: str = NEMO, grant: str | None = None,
          request: str = JOB, purpose: str = "provider_sharing", categories=CATS,
          ttl=300) -> dict:
    return {"handle_sha256": digest(n), "user_id": user, "provider_org_id": provider,
            "grant_ref": grant or W["grant"], "request_id": request, "purpose": purpose,
            "categories": list(categories), "ttl_s": ttl}


def redeem(n: int = 1, *, user: str = DEV, provider: str = NEMO) -> dict:
    return {"handle_sha256": digest(n), "user_id": user, "provider_org_id": provider}


def held(conn, request: str = JOB) -> bool:
    return ok(conn, "lab_content_ref_held", {"org_id": W["c1_org"], "request_id": request})["held"]


def at(conn, iso: str, seconds: float) -> bool:
    """The answer's timestamp is infrx.now() + `seconds`."""
    return conn.execute("select %s::timestamptz = infrx.now() + make_interval(secs => %s)",
                        (iso, seconds)).fetchone()[0]


def retained_until(conn, request: str = JOB, days: int = 30) -> str:
    return conn.execute("select to_jsonb(created_at + make_interval(days => %s)) #>> '{}' "
                        "from infrx.jobs where request_id = %s", (days, request)).fetchone()[0]


def same_instant(conn, a: str, b: str) -> bool:
    return conn.execute("select %s::timestamptz = %s::timestamptz", (a, b)).fetchone()[0]


def door(conn, user: str | None, *args):
    """(SQLSTATE or None, answer or refusal code) of the session door as `user`."""
    sql = "select public.lab_content_ref_issue(%s, %s::uuid, %s, %s::uuid, %s)"
    try:
        with conn.transaction():
            conn.execute(checks._jwt(user) if user else checks.SESSIONS["anon"])
            answer = conn.execute(sql, args).fetchone()[0]
            conn.execute("reset role")
    except psycopg.Error as refused:
        code = str(refused).split(":", 1)[0] if refused.sqlstate == "P0001" else None
        return refused.sqlstate, code
    return None, answer


def flag(conn, enabled: bool) -> None:
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('lab_content', %s, 'c2rpc-test', 'test') on conflict (name) do update set "
                 "enabled = excluded.enabled", (enabled,))


def advance(conn, seconds: float) -> None:
    t.advance(conn, seconds)


def seed(conn) -> None:
    """test_d7_lab_data's world, then C1's content grant to NEMO (version 2 of the pair),
    BOTH's to OTHER, and one admitted job each for C1 and BOTH."""
    t.seed(conn)
    W["c1_org"] = l2.org(conn, C1)
    W["grant"] = grant_ref(v2.AccessGrant.model_validate(content_grant(conn)))
    other = ok(conn, "lab_put_access_grant", l2.scope(
        conn, owner=BOTH, provider=OTHER, model_ids=[l2.OTHER_MODEL], categories=list(CATS),
        purposes=["provider_sharing"]))
    W["other_grant"] = grant_ref(v2.AccessGrant.model_validate(other))
    conn.execute(cc.credit_job(JOB, "job_c2rpc_1", W["c1_org"], cc.wallet_of(conn, C1)))
    conn.execute(cc.credit_job(JOB_BOTH, "job_c2rpc_2", l2.org(conn, BOTH),
                               cc.wallet_of(conn, BOTH)))


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads a content-ref or tombstone table or executes an
    infrx RPC; the platform role reads the tables and writes them only through the RPCs;
    rows are append-only; the session door is authenticated's alone (never anon)."""
    probes = [f"select count(*) from infrx.{name}" for name in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached content refs: {reached}"
    unread = [n for n in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(n)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [n for n in TABLES if not (cc.refused_as(conn, "service", f"delete from infrx.{n}")
                                         or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    unguarded = [n for n in TABLES if not conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"infrx.{n}",)
    ).fetchone()[0]]
    assert not unguarded, f"row security is off on {unguarded}"
    ok(conn, "lab_content_ref_issue", issue(90))
    edited = cc.attempt(conn, "update infrx.lab_content_refs set expires_at = expires_at + "
                        "interval '1 day'")
    assert edited is not None, "an issued ref was extended in place"
    acl = conn.execute("select proacl::text from pg_proc where oid = %s::regprocedure",
                       (DOOR,)).fetchone()[0]
    grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
    assert "authenticated" in grantees and "anon" not in grantees and "" not in grantees, acl
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; door {grantees}"


@rolled_back
def check_issue_binds_the_grant_recipient_user_and_expiry(conn) -> str:
    """C2: a ref is bound to the named grant (its CURRENT version answered), the recipient
    provider, the issuing user, the grantor's own request and its purpose and categories;
    only the handle's digest is stored; it expires at the earliest of the TTL, the current
    grant's expiry and the job's age plus the grant's retention."""
    got = ok(conn, "lab_content_ref_issue", issue(1))
    assert {k: v for k, v in got.items() if k != "expires_at"} == {
        "grant_id": W["grant"].split(":")[3].split("@")[0], "grant_version": 2,
        "grantor_org_id": W["c1_org"], "request_id": JOB, "purpose": "provider_sharing"}, got
    assert at(conn, got["expires_at"], 300), got["expires_at"]
    row = conn.execute("select provider_org_id::text, user_id::text, grantor_org_id::text, "
                       "categories, purpose from infrx.lab_content_refs where handle_sha256 = "
                       "%s", (digest(1),)).fetchone()
    assert row == (NEMO, DEV, W["c1_org"], list(CATS), "provider_sharing"), row
    content_grant(conn, expires_at=conn.execute(
        "select infrx.now() + interval '100 seconds'").fetchone()[0].isoformat())
    bounded = ok(conn, "lab_content_ref_issue", issue(2, ttl=900))
    assert bounded["grant_version"] == 3 and at(conn, bounded["expires_at"], 100), bounded
    content_grant(conn)
    advance(conn, 30 * 86400 - 60)
    late = ok(conn, "lab_content_ref_issue", issue(3, ttl=900))
    assert same_instant(conn, late["expires_at"], retained_until(conn)), late
    advance(conn, 60)
    assert refusal(conn, "lab_content_ref_issue", issue(4)) == "result_expired"
    return "bound: current version, TTL 300; grant expiry 100 s; retention bound; then gone"


@rolled_back
def check_issue_refuses_in_the_contracts_order(conn) -> str:
    """C2's refusals, in its order: invalid_request (the shape) -> not_found (a grant not TO
    this provider, a forged ref, a request not the grantor's job) -> forbidden (a viewer, a
    consumer, another provider's member, a purpose or category or model the current grant
    does not name, a revoked membership) -> result_expired -> state_conflict (a reissued
    handle - after every other rule, so a refused caller learns nothing about it)."""
    bad = {"ttl_0": issue(ttl=0), "ttl_901": issue(ttl=901), "ttl_text": issue(ttl="x"),
           "no_category": issue(categories=()), "unknown_category": issue(categories=["x"]),
           "unknown_purpose": issue(purpose="resale"), "digest": {**issue(), "handle_sha256":
                                                                  "tc_raw-handle"},
           "user": issue(user="dev@nemo")}
    got = {k: refusal(conn, "lab_content_ref_issue", v) for k, v in bad.items()}
    assert got == dict.fromkeys(bad, "invalid_request"), got
    forged = W["grant"][:-1] + ("0" if W["grant"][-1] != "0" else "1")
    missing = {"foreign_grant": issue(grant=W["other_grant"]), "forged": issue(grant=forged),
               "not_the_recipient": issue(provider=OTHER, user=BOTH),
               "other_grantors_job": issue(request=JOB_BOTH),
               "unknown_request": issue(request=uid(9, 0xc2))}
    got = {k: refusal(conn, "lab_content_ref_issue", v) for k, v in missing.items()}
    assert got == dict.fromkeys(missing, "not_found"), got
    ok(conn, "lab_content_ref_issue", issue(1))
    denied = {"viewer": issue(1, user=VIEWER), "consumer": issue(1, user=C1),
              "other_member": issue(1, user=BOTH), "purpose": issue(1, purpose="external_judging"),
              "category": issue(1, categories=[*CATS, "media"])}
    got = {k: refusal(conn, "lab_content_ref_issue", v) for k, v in denied.items()}
    assert got == dict.fromkeys(denied, "forbidden"), got
    assert refusal(conn, "lab_content_ref_issue", issue(1)) == "state_conflict", "reissued"
    with conn.transaction(force_rollback=True):
        content_grant(conn, model_ids=[])
        assert refusal(conn, "lab_content_ref_issue", issue(5)) == "forbidden", "model"
    with conn.transaction(force_rollback=True):
        conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                     "where user_id = %s", (DEV,))
        assert refusal(conn, "lab_content_ref_issue", issue(5)) == "forbidden", "revoked"
    with conn.transaction(force_rollback=True):
        advance(conn, 2 * 86400)
        content_grant(conn, retention_days=1)
        assert refusal(conn, "lab_content_ref_issue", issue(5)) == "result_expired"
    return f"{len(bad)} invalid, {len(missing)} not_found, {len(denied) + 2} forbidden, " \
           "retention gone, reissue state_conflict"


@rolled_back
def check_redeem_rechecks_every_rule(conn) -> str:
    """C2: every read re-checks the CURRENT rows - only the issued provider AND user redeem
    (anyone else: not_found); a narrowed grant, a revocation or a revoked membership fails
    closed however new the ref; a re-grant reopens it (rights, not a snapshot); its own
    expiry is result_expired."""
    issued = ok(conn, "lab_content_ref_issue", issue(10, ttl=600))
    got = ok(conn, "lab_content_ref_redeem", redeem(10))
    assert got == issued, (got, issued)
    others = {"admin": redeem(10, user=ADMIN), "other": redeem(10, user=BOTH, provider=OTHER),
              "other_provider": redeem(10, provider=OTHER), "unknown": redeem(11)}
    got = {k: refusal(conn, "lab_content_ref_redeem", v) for k, v in others.items()}
    assert got == dict.fromkeys(others, "not_found"), got
    content_grant(conn, purposes=["training"])
    assert refusal(conn, "lab_content_ref_redeem", redeem(10)) == "forbidden", "narrowed"
    content_grant(conn)
    assert ok(conn, "lab_content_ref_redeem", redeem(10))["grant_version"] == 4, "re-grant"
    with conn.transaction(force_rollback=True):
        conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                     "where user_id = %s", (DEV,))
        assert refusal(conn, "lab_content_ref_redeem", redeem(10)) == "forbidden", "member"
    with conn.transaction(force_rollback=True):
        advance(conn, 600)
        assert refusal(conn, "lab_content_ref_redeem", redeem(10)) == "result_expired"
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": W["c1_org"],
                                         "recipient_provider_org_id": NEMO})
    assert refusal(conn, "lab_content_ref_redeem", redeem(10)) == "forbidden", "revoked"
    return "provider+user bound; narrowed/revoked/member forbidden; re-grant reopens; expiry"


@rolled_back
def check_retention_is_rechecked_at_redeem(conn) -> str:
    """C2 / T3: a ref issued while the content was retained fails with result_expired once
    the CURRENT grant's retention no longer covers the job - and its answer's expiry is
    the earlier of the two bounds while both hold."""
    advance(conn, 2 * 86400)
    issued = ok(conn, "lab_content_ref_issue", issue(20, ttl=900))
    assert at(conn, issued["expires_at"], 900)
    content_grant(conn, retention_days=3)
    assert ok(conn, "lab_content_ref_redeem", redeem(20))["expires_at"] == issued["expires_at"]
    content_grant(conn, retention_days=1)
    assert refusal(conn, "lab_content_ref_redeem", redeem(20)) == "result_expired"
    assert not held(conn), "an out-of-retention ref holds the object"
    return "retention shortened below the job's age: result_expired, holds nothing"


@rolled_back
def check_held_is_a_live_redeemable_ref(conn) -> str:
    """T3's holds: a request is held while a ref to it is unexpired and still redeemable -
    not before a ref exists, not for another request, not while the grant is revoked (a
    re-grant holds again), never after the ref's expiry."""
    assert not held(conn), "held with no ref"
    ok(conn, "lab_content_ref_issue", issue(30))
    assert held(conn) and not held(conn, JOB_BOTH), "held the wrong request"
    assert not ok(conn, "lab_content_ref_held", {"org_id": l2.org(conn, BOTH),
                                                 "request_id": JOB})["held"], "another org"
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": W["c1_org"],
                                         "recipient_provider_org_id": NEMO})
    assert not held(conn), "a revoked grant holds content"
    content_grant(conn)
    assert held(conn), "a re-granted ref no longer holds"
    advance(conn, 301)
    assert not held(conn), "an expired ref holds content"
    return "held only while unexpired and redeemable"


@rolled_back
def check_the_door_is_the_sessions_own_user(conn) -> str:
    """LAB-ACCESS: the session door is closed until its own flag is on (55000 - the launched
    App's database answers nothing); then it issues as auth.uid() only, over request and
    response content for 300 s; a viewer, a consumer-only user and another provider's
    member get the RPC's `forbidden`; anon cannot execute it."""
    a = (digest(40), NEMO, W["grant"], JOB, "provider_sharing")
    assert door(conn, DEV, *a) == ("55000", None), "the door answered with no flag row"
    flag(conn, False)
    assert door(conn, DEV, *a) == ("55000", None), "the door answered with the flag off"
    flag(conn, True)
    state, got = door(conn, DEV, *a)
    assert state is None and got["grantor_org_id"] == W["c1_org"], (state, got)
    assert at(conn, got["expires_at"], 300), got
    row = conn.execute("select user_id::text, categories from infrx.lab_content_refs where "
                       "handle_sha256 = %s", (digest(40),)).fetchone()
    assert row == (DEV, list(CATS)), row
    refused = {u: door(conn, u, digest(41), NEMO, W["grant"], JOB, "provider_sharing")
               for u in (VIEWER, C1, BOTH)}
    assert refused == dict.fromkeys(refused, ("P0001", "forbidden")), refused
    assert door(conn, None, *a)[0] == "42501", "anon executed the door"
    return "flag-gated; auth.uid() only; others forbidden; anon 42501"


def check_one_handle_is_issued_once_under_contention(conn) -> str:
    """C2 race (commits): two issuers of one digest at once - exactly one ref, the other
    `state_conflict`."""
    gate, answers = threading.Barrier(2), [None, None]

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, "lab_content_ref_issue", issue(50))["grant_version"]
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(map(str, answers)) == ["2", "state_conflict"], answers
    n = conn.execute("select count(*) from infrx.lab_content_refs where handle_sha256 = %s",
                     (digest(50),)).fetchone()[0]
    assert n == 1, n
    return f"racing issues: {sorted(map(str, answers))}"


@rolled_back
def check_tombstones_and_bounds_gate_the_samples(conn) -> str:
    """N3 / DATA-RIGHTS: the permitted samples are D7's accessible ones minus the tombstoned
    (permanent, first reason stands, a re-grant never resurrects) minus those past their
    content bound (write-once; another bound is idempotency_conflict); one provider's
    tombstones never touch another's datasets."""
    ds = t.publish(conn, t.manifest(uid(1, 0xc3), n=3, tag=0xc3))
    s1, s2, s3 = (uid(i, 0xc3) for i in (1, 2, 3))
    args = {"provider_org_id": NEMO, "dataset_ref": ds}
    assert ok(conn, "lab_permitted_samples", args) == [s1, s2, s3]
    stone = {"provider_org_id": NEMO, "sample_ids": [s1], "reason": "grant_revoked"}
    assert ok(conn, "lab_tombstone_samples", stone) == {"tombstoned": [s1]}
    assert ok(conn, "lab_tombstone_samples", {**stone, "reason": "deleted"}) == \
        {"tombstoned": []}
    until = conn.execute("select to_jsonb(infrx.now() + interval '60 seconds') #>> '{}'"
                         ).fetchone()[0]
    bound = {"provider_org_id": NEMO, "bounds": [{"sample_id": s2, "content_until": until}]}
    ok(conn, "lab_bound_samples", bound)
    ok(conn, "lab_bound_samples", bound)
    assert refusal(conn, "lab_bound_samples", {**bound, "bounds": [
        {"sample_id": s2, "content_until": "2030-01-01T00:00:00Z"}]}) == "idempotency_conflict"
    assert ok(conn, "lab_permitted_samples", args) == [s2, s3]
    assert ok(conn, "lab_blocked_samples", args) == {s1: "grant_revoked"}
    advance(conn, 60)
    assert ok(conn, "lab_permitted_samples", args) == [s3]
    assert ok(conn, "lab_blocked_samples", args) == {s1: "grant_revoked", s2: "content_expired"}
    content_grant(conn)
    assert ok(conn, "lab_permitted_samples", args) == [s3], "a re-grant resurrected"
    ok(conn, "lab_tombstone_samples", {"provider_org_id": OTHER, "sample_ids": [s3],
                                       "reason": "deleted"})
    assert ok(conn, "lab_permitted_samples", args) == [s3], "another provider's tombstone"
    assert ok(conn, "lab_blocked_samples", {**args, "provider_org_id": OTHER}) == {}
    assert refusal(conn, "lab_tombstone_samples", {**stone, "reason": ""}) == "invalid_request"
    return "permitted = accessible - tombstoned - past bound; permanent; provider-scoped"


def check_the_adapters_compose(conn) -> str:
    """`PgContentRefs` is C2's `ContentRefs`: typed bindings, and C2's typed refusals
    (NotFound, Forbidden, Gone, InvalidRequest, Conflict); `PgSampleRestrictions` round
    trips N3's gate."""
    ensure_ref_binding()
    refs = PgContentRefs(connector(pgharness.dsn(conn.info.dbname)))
    gate = PgSampleRestrictions(connector(pgharness.dsn(conn.info.dbname)))
    with conn.transaction():
        ds = t.publish(conn, t.manifest(uid(1, 0xc4), n=2, tag=0xc4))

    async def refused(coro) -> str:
        """The refusal's class (`ResultExpired` is C2's `Gone`)."""
        try:
            await coro
        except errors.DomainError as failed:
            return type(failed).__name__
        return "answered"

    async def go() -> list:
        kw = {"user_id": DEV, "provider_org_id": NEMO, "grant_ref": W["grant"],
              "request_id": JOB, "purpose": "provider_sharing", "categories": CATS}
        bound = await refs.issue(handle_sha256=digest(60), ttl_s=300, **kw)
        got = [type(bound).__name__, bound.grant_version, bound.grantor_org_id == W["c1_org"]]
        again = await refs.redeem(handle_sha256=digest(60), user_id=DEV, provider_org_id=NEMO)
        got.append(again == bound)
        got.append(await refs.held(W["c1_org"], JOB))
        got.append(await refused(refs.issue(handle_sha256=digest(60), ttl_s=300, **kw)))
        got.append(await refused(refs.issue(handle_sha256=digest(61), ttl_s=0, **kw)))
        got.append(await refused(refs.issue(handle_sha256=digest(61), ttl_s=300,
                                            **{**kw, "user_id": VIEWER})))
        got.append(await refused(refs.redeem(handle_sha256=digest(60), user_id=ADMIN,
                                             provider_org_id=NEMO)))
        got.append(await gate.tombstone([uid(1, 0xc4)], provider_org_id=NEMO, reason="deleted"))
        got.append(await gate.blocked(ds, provider_org_id=NEMO))
        got.append(await gate.permitted(ds, provider_org_id=NEMO))
        return got
    try:
        got = asyncio.run(go())
    except Exception as failed:         # R40: a broken answer is this check's assertion
        raise AssertionError(f"{type(failed).__name__}: {failed}") from None
    assert got == ["RefBinding", 2, True, True, True, "StateConflict", "InvalidRequest",
                   "Forbidden", "NotFound", [uid(1, 0xc4)], {uid(1, 0xc4): "deleted"},
                   [uid(2, 0xc4)]], got
    with conn.transaction():
        advance(conn, 301)
    try:
        assert asyncio.run(refused(refs.redeem(handle_sha256=digest(60), user_id=DEV,
                                               provider_org_id=NEMO))) == "ResultExpired"
    finally:
        with conn.transaction():
            advance(conn, -301)
    return f"adapters: {got[:5]}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_issue_binds_the_grant_recipient_user_and_expiry,
    check_issue_refuses_in_the_contracts_order,
    check_redeem_rechecks_every_rule,
    check_retention_is_rechecked_at_redeem,
    check_held_is_a_live_redeemable_ref,
    check_the_door_is_the_sessions_own_user,
    check_one_handle_is_issued_once_under_contention,
    check_tombstones_and_bounds_gate_the_samples,
    check_the_adapters_compose)}


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
def test_c2rpc_content(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
