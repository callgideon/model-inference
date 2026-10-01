"""AP-03 on a REAL PostgreSQL (ap3, 57553): API-KEYGRANT and the operator mutations.

    INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/ap03/test_actions_pg.py

One migrated, seeded database per module (`tests/g/ops/pgworld`: the 0001-0059 chain, every
flag on, GoTrue's confirmation column); each case makes its own individuals. The repository
runs on `service_role` connections with the actor bound as the JWT subject, as the composed
API will. The race cases are two OS processes (`racer.py`), each its own API instance,
held behind a table lock the test owns until every request of both is waiting on the
database, then released at once. Skips visibly off the ap3 key (never d1).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

import httpx
import pytest

from infrx.auth.context import KEY_COLUMNS, AuthResolver
from infrx.console.actions import ConsoleActions
from infrx.contracts import api, errors
from infrx.state.jobstore import connector

from tests.d import pgharness

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "ap3" else \
    "PostgreSQL only on the ap3 task-local key (INFRX_D_TASK=ap3)"
pytestmark = [pytest.mark.pg, pytest.mark.skipif(
    _reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")]

API_DIR = pathlib.Path(__file__).resolve().parents[2]
REASON = "support ticket 7"


@pytest.fixture(scope="module")
def w():
    from tests.g.ops import pgworld
    return pgworld.world("ap3_actions")


def run(coro):
    return asyncio.run(coro)


def refused(kind, coro) -> None:
    """The typed refusal, asserted: any other exception (a raw database error, a crash) is
    an assertion failure naming it, never a pass."""
    try:
        run(coro)
    except Exception as exc:     # the type is the assertion
        assert isinstance(exc, kind), f"{type(exc).__name__} instead of {kind.__name__}: {exc}"
        return
    raise AssertionError(f"no {kind.__name__}: the write was accepted")


def person(w, *, confirmed=True, operator=False) -> api.Actor:
    from tests.g.ops import pgworld
    user = pgworld.individual(w, confirmed=confirmed)
    if operator:
        w.owner.execute("update public.profiles set is_operator = true where id = %s", (user,))
    return api.Actor(audience="session", user_id=user, org_id=pgworld.personal_org(w, user),
                     operator=operator)


def actions(w) -> ConsoleActions:
    return ConsoleActions(connector(w.dsn))


def keys_of(w, org) -> list[tuple]:
    return w.owner.execute("select id::text, key_hash, prefix, name, user_id::text, revoked_at "
                           "from public.api_keys where org_id = %s order by created_at",
                           (org,)).fetchall()


def entitlements(w, user) -> int:
    return w.one("select count(*) from infrx.signup_entitlements where user_id = %s", (user,))


# ------------------------------------------------------------------------- 03a keys
def gateway_reads(w):
    """The gateway's PostgREST `api_keys` lookup, answered from this database."""
    def handler(request):
        key_hash = request.url.params["key_hash"].removeprefix("eq.")
        cols = request.url.params["select"].split(",")
        assert cols == KEY_COLUMNS.split(","), cols
        rows = w.owner.execute("select id, org_id, revoked_at, audience, user_id, created_by, "
                               "provider_org_id, endpoint_id from public.api_keys "
                               "where key_hash = %s", (key_hash,)).fetchall()
        return httpx.Response(200, json=[{c: (None if v is None else str(v))
                                          for c, v in zip(cols, r)} for r in rows])
    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


def test_keys_pg__the_first_create_reveals_the_secret_once_and_the_gateway_authenticates_it(w):
    """Oracle: the secret is not the App's `sk-infrx-` + 40 base62 shape, the stored hash is
    not the gateway's sha256 hex (the key would never authenticate), or the row is not the
    actor's individual's consumer key."""
    from tests.g import support
    actor = person(w)
    created = run(actions(w).create_key(actor, "  laptop ", "idem-1"))
    assert created.secret_returned and not created.replayed and created.secret is not None
    secret = created.secret
    assert secret.startswith("sk-infrx-") and len(secret) == 49 and secret[9:].isalnum()
    [(key_id, key_hash, prefix, name, user, revoked)] = keys_of(w, actor.org_id)
    assert (key_id, key_hash, prefix, name, user, revoked) == (
        created.key.key_id, hashlib.sha256(secret.encode()).hexdigest(), secret[:17], "laptop",
        actor.user_id, None)
    assert created.key.prefix == prefix and created.key.revoked_at is None

    rt = support.runtime(support.settings(), sb=gateway_reads(w))
    request = httpx.Request("POST", "http://g/v1/chat/completions",
                            headers={"authorization": f"Bearer {secret}"})
    context = run(AuthResolver(rt).context(request))
    assert (context.key_id, context.org_id, context.user_id, context.audience.value) == (
        key_id, actor.org_id, actor.user_id, "consumer")


def test_keys_pg__a_replay_returns_the_same_key_without_the_secret(w):
    """Oracle: a retry after a lost response minted a second key, or revealed a secret."""
    actor = person(w)
    first = run(actions(w).create_key(actor, "ci", "idem-r"))
    again = run(actions(w).create_key(actor, "ci", "idem-r"))
    assert (again.replayed, again.secret, again.secret_returned) == (True, None, False)
    assert again.key == first.key
    assert len(keys_of(w, actor.org_id)) == 1
    assert w.one("select count(*) from infrx.audit_entries where target_org_id = %s "
                 "and action = 'admin_key_issue'", (actor.org_id,)) == 1


def test_keys_pg__a_different_name_under_the_same_key_is_a_conflict(w):
    """Oracle: the replay ignored the body (answered the first key for another request) or
    minted a second key under a used idempotency key."""
    actor = person(w)
    run(actions(w).create_key(actor, "one", "idem-c"))
    refused(errors.IdempotencyConflict, actions(w).create_key(actor, "two", "idem-c"))
    assert [k[3] for k in keys_of(w, actor.org_id)] == ["one"]


def test_keys_pg__the_idempotency_key_is_scoped_to_the_account(w):
    """Oracle: two accounts choosing the same Idempotency-Key collided (one got the other's
    key, or a conflict)."""
    a, b = person(w), person(w)
    ka = run(actions(w).create_key(a, "same", "shared-key"))
    kb = run(actions(w).create_key(b, "same", "shared-key"))
    assert ka.key.key_id != kb.key.key_id and kb.secret is not None
    assert [k[0] for k in keys_of(w, b.org_id)] == [kb.key.key_id]


def race(w, kind: str, actor: api.Actor, lock: str, *, per_process: int = 3) -> list[dict]:
    """Two API processes, `per_process` concurrent calls each, released together."""
    holder = pgharness.connect(w.database, autocommit=False)
    from psycopg import sql
    holder.execute(sql.SQL("lock table {} in access exclusive mode")
                   .format(sql.Identifier(*lock.split("."))))
    procs = [subprocess.Popen(
        [sys.executable, "-m", "tests.ap03.racer", w.dsn, kind, actor.user_id or "",
         actor.org_id or "", str(per_process)], cwd=API_DIR, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        deadline = time.monotonic() + 60
        with pgharness.connect(w.database) as watch:
            while (watch.execute("select count(*) from pg_stat_activity where datname = %s "
                                 "and wait_event_type = 'Lock'", (w.database,)).fetchone()
                   or (0,))[0] < 2 * per_process:
                assert time.monotonic() < deadline, "the racers never all reached the database"
                assert all(p.poll() is None for p in procs), [p.communicate() for p in procs]
                time.sleep(0.05)
    finally:
        holder.rollback()
        holder.close()
    answers = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        answers += json.loads(out)
    return answers


def test_keys_pg__two_processes_racing_one_idempotency_key_create_exactly_one_key(w):
    """API-KEYGRANT: six creates under one Idempotency-Key from two API processes, all past
    the replay lookup before any commits. Oracle: a second key row, a 409/500 for a
    same-body retry, or more (or fewer) than one response carrying the secret."""
    actor = person(w)
    answers = race(w, "key", actor, "public.api_keys")
    assert sorted(a["status"] for a in answers) == [200] * 5 + [201], answers
    assert len({a["body"]["key"]["key_id"] for a in answers}) == 1
    secrets = [a["body"]["secret"] for a in answers if a["body"]["secret"] is not None]
    [row] = keys_of(w, actor.org_id)
    assert len(secrets) == 1 and hashlib.sha256(secrets[0].encode()).hexdigest() == row[1]


def test_keys_pg__an_unverified_individual_cannot_create_a_key(w):
    """Oracle: the backend's service role skipped `consumer_may_create_key`."""
    actor = person(w, confirmed=False)
    refused(errors.Forbidden, actions(w).create_key(actor, "nope", "idem-u"))
    assert keys_of(w, actor.org_id) == []


def test_keys_pg__an_organization_the_actor_does_not_own_is_refused(w):
    """Oracle: the backend trusted the actor's org id without the owner check the RLS
    insert policy made (`is_org_owner`), so a mis-resolved actor minted into another
    account."""
    a, b = person(w), person(w)
    forged = a.model_copy(update={"org_id": b.org_id})
    refused(errors.Forbidden, actions(w).create_key(forged, "x", "idem-f"))
    assert keys_of(w, b.org_id) == []


def test_keys_pg__a_suspended_account_revokes_but_cannot_create(w):
    """R33: suspension refuses new work only. Oracle: a suspended account minted a key, or
    could not revoke one."""
    actor = person(w)
    created = run(actions(w).create_key(actor, "before", "idem-s1"))
    w.owner.execute("select infrx.set_suspension(%s, true, 'abuse', 'test', 'suspended', "
                    "'susp-' || %s)", (actor.org_id, actor.org_id))
    refused(errors.OrgSuspended, actions(w).create_key(actor, "after", "idem-s2"))
    revoked = run(actions(w).revoke_key(actor, created.key.key_id, None))
    assert revoked.revoked_at is not None and len(keys_of(w, actor.org_id)) == 1


def test_keys_pg__revocation_is_idempotent_scoped_and_conflicts_on_a_reused_key(w):
    """Oracle: a second revoke moved `revoked_at`, another account's key was revocable (or
    answered other than not_found), or one Idempotency-Key revoked two keys."""
    actor, other = person(w), person(w)
    k1 = run(actions(w).create_key(actor, "k1", "i1")).key
    k2 = run(actions(w).create_key(actor, "k2", "i2")).key
    theirs = run(actions(w).create_key(other, "t", "i3")).key
    first = run(actions(w).revoke_key(actor, k1.key_id, "rv-1"))
    again = run(actions(w).revoke_key(actor, k1.key_id, "rv-1"))
    assert first.revoked_at is not None and again.revoked_at == first.revoked_at
    refused(errors.IdempotencyConflict, actions(w).revoke_key(actor, k2.key_id, "rv-1"))
    refused(errors.NotFound, actions(w).revoke_key(actor, theirs.key_id, "rv-2"))
    assert [k[5] is None for k in keys_of(w, actor.org_id)] == [False, True]
    assert keys_of(w, other.org_id)[0][5] is None


# ------------------------------------------------------------------------- 03b grant
def test_grant_pg__a_claim_grants_10000_credit_once_and_replays(w):
    """Oracle: the claim is not the campaign's individual grant (amount, unit), or a second
    claim granted again."""
    actor = person(w)
    first = run(actions(w).claim_grant(actor))
    again = run(actions(w).claim_grant(actor))
    assert (first.status, first.credit) == ("granted", api.Money(amount="10000.00000000",
                                                                 unit="CREDIT"))
    assert (again.status, again.credit, again.granted_at) == ("replayed", first.credit,
                                                              first.granted_at)
    assert entitlements(w, actor.user_id) == 1
    assert w.one("select campaign_version from infrx.signup_entitlements where user_id = %s",
                 (actor.user_id,)) == "consumer-v1"


def test_grant_pg__an_unverified_individual_is_not_granted(w):
    """Oracle: an unverified account received CREDIT."""
    actor = person(w, confirmed=False)
    claim = run(actions(w).claim_grant(actor))
    assert (claim.status, claim.credit, claim.granted_at) == ("unverified", None, None)
    assert entitlements(w, actor.user_id) == 0


def test_grant_pg__two_processes_claiming_concurrently_land_one_grant(w):
    """API-KEYGRANT: six claims for one individual from two API processes at once. Oracle:
    more than one entitlement or ledger row, more than one `granted` answer, or a racer
    that did not see the one grant."""
    from tests.g.ops import pgworld
    actor = person(w)
    answers = race(w, "grant", actor, "infrx.signup_entitlements")
    assert all(a["status"] == 200 for a in answers), answers
    assert sorted(a["body"]["status"] for a in answers) == ["granted"] + ["replayed"] * 5
    assert all(a["body"]["credit"] == {"amount": "10000.00000000", "unit": "CREDIT"}
               for a in answers), answers
    assert len({a["body"]["granted_at"] for a in answers}) == 1, answers
    assert entitlements(w, actor.user_id) == 1 and pgworld.signup_rows(w, str(actor.user_id)) == 1


# ------------------------------------------------------------------------- 03d operator
def test_operator_pg__an_adjustment_applies_once_and_records_actor_and_reason(w):
    """Oracle: a duplicate request moved money twice, or the ledger lost who and why."""
    operator, consumer = person(w, operator=True), person(w)
    run(actions(w).claim_grant(consumer))
    first = run(actions(w).adjust_credit(operator, str(consumer.user_id), "2.5", REASON, "adj-1"))
    again = run(actions(w).adjust_credit(operator, str(consumer.user_id), "2.5", REASON, "adj-1"))
    assert (first.replayed, again.replayed) == (False, True)
    assert first.amount == api.Money(amount="2.50000000", unit="CREDIT")
    rows = w.owner.execute(
        "select l.actor, l.reason from infrx.credit_ledger l join infrx.credit_wallets c "
        "using (wallet_id) where c.owner_user_id = %s and l.kind = 'operator_adjustment'",
        (consumer.user_id,)).fetchall()
    assert rows == [(f"operator:{operator.user_id}", REASON)]


def test_operator_pg__suspension_and_revocation_apply_once_with_an_audit_receipt(w):
    """Oracle: a duplicate request audited twice, or the receipt lost the reason/actor."""
    operator, consumer = person(w, operator=True), person(w)
    key = run(actions(w).create_key(consumer, "k", "ik")).key
    s1 = run(actions(w).set_suspension(operator, str(consumer.org_id), True, REASON, "s-1"))
    s2 = run(actions(w).set_suspension(operator, str(consumer.org_id), True, REASON, "s-1"))
    assert (s1.replayed, s2.replayed, s1.suspended) == (False, True, True)
    assert w.one("select suspended from public.organizations where id = %s",
                 (consumer.org_id,)) is True
    r1 = run(actions(w).revoke_key_as_operator(operator, key.key_id, REASON, "r-1"))
    r2 = run(actions(w).revoke_key_as_operator(operator, key.key_id, REASON, "r-1"))
    assert (r1.replayed, r2.replayed) == (False, True)
    receipts = w.owner.execute(
        "select action, actor_principal, reason from infrx.audit_entries "
        "where idempotency_key in ('app-operator:suspension:s-1', 'app-operator:revoke:r-1') "
        "order by action").fetchall()
    assert receipts == [("admin_key_revoke", f"operator:{operator.user_id}", REASON),
                        ("admin_set_suspension", f"operator:{operator.user_id}", REASON)]


def test_operator_pg__a_forged_operator_actor_is_refused_by_the_database(w):
    """The repository's own check passes a forged `operator=True`; the SQL guard
    (`console_operator`: profiles.is_operator of the bound subject) must still refuse.
    Oracle: the actor was not bound as the JWT subject, so the guard saw no one - or the
    service role - and the write happened."""
    forged = person(w).model_copy(update={"operator": True})
    victim = person(w)
    refused(errors.Forbidden, actions(w).set_suspension(forged, str(victim.org_id), True, REASON, "forged-1"))
    assert w.one("select suspended from public.organizations where id = %s",
                 (victim.org_id,)) is False
