#!/usr/bin/env python3
"""D6F / WR-C3F-1 (wave-5 LW2, lab-sql; the feedback lane's C3F request): the two session doors
of `0038_feedback_doors.sql` on real PostgreSQL, called with each member's own session -
FEEDBACK-ACK (provenance is the server's) and LAB-ACCESS (L2's rule on the database clock).

World: test_l2sql_access's role matrix - C1 and BOTH consumers (BOTH also OTHER's developer),
NEMO (DEV developer, ADMIN administrator, VIEWER viewer) and OTHER - plus the `feedback` flag,
one CREDIT job admitted for C1 on NEMO's model, and C1's grant of `feedback` for
`provider_sharing` of that model to NEMO. Each `check_*` is the check a mutant in
`code_mutants_d6f.py` (`DOORS`) must break.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d6f_doors.py
"""
from __future__ import annotations

import psycopg
import pytest
from infrx.contracts.conformance import builders as b
from infrx.state import migrations
from infrx.state.jobstore import domain_error
from psycopg.types.json import Jsonb

from . import checks
from . import checks_admission as ca
from . import checks_credit as cc
from . import pgharness
from . import test_l2sql_access as l2

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d6fd"

NEMO, OTHER, C1, BOTH = l2.NEMO, l2.OTHER, l2.C1, l2.BOTH
DEV, VIEWER = l2.DEV, l2.VIEWER
ok, rolled_back = l2.ok, l2.rolled_back
W: dict[str, str] = {}


def door(conn, user: str | None, fn: str, args: dict):
    """(refusal code or None, answer) of one door call as `user` (None: anon); a refusal
    is its domain code, or the SQLSTATE when it is not one (42501: no EXECUTE)."""
    try:
        with conn.transaction():
            conn.execute(checks._jwt(user) if user else checks.SESSIONS["anon"])
            answer = conn.execute(f"select public.{fn}(%s)", (Jsonb(args),)).fetchone()[0]
            conn.execute("reset role")
    except psycopg.Error as refused:
        return getattr(domain_error(refused), "code", refused.sqlstate), None
    return None, answer


def submit(conn, user: str = C1, *, key: str = "k-1", **over):
    return door(conn, user, "submit_feedback", {"request_id": W["job"], "name": "rating",
                                                "value": 4, "idempotency_key": key, **over})


def review(conn, user: str = DEV, provider: str = NEMO, request: str | None = None):
    return door(conn, user, "lab_review_feedback", {"provider_org_id": provider,
                                                    "request_id": request or W["job"]})


def share(conn, **over) -> dict:
    return ok(conn, "lab_put_access_grant", l2.scope(
        conn, **{"categories": ["feedback"], "purposes": ["provider_sharing"], **over}))


def seed(conn) -> None:
    l2.seed(conn)
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                 "values ('feedback', true, 'rig', 'doors') on conflict (name) do update "
                 "set enabled = true")
    world = ca.World(conn)
    request = ca.credit_request(world, ca.C1_KEY, cc.personal_org(conn, C1),
                                model="nemostation/marlin-2b")
    ca.admit(conn, request, b.idem(request, request.request_id), regime="credit")
    W["job"] = request.request_id
    share(conn)


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_submit_takes_the_signal_and_derives_the_rest(conn) -> str:
    """FEEDBACK-ACK: a member's feedback on its org's request is stored with the SERVER's
    provenance (author = the session user, channel console, role customer); a provenance
    field in the body is refused; a replay of the key is the same row; a non-member, an
    unknown or malformed id are `not_found`; anon cannot execute; the flag gates it."""
    status, row = submit(conn)
    assert status is None, status
    stored = conn.execute("select author_principal, channel, author_role, org_id::text from "
                          "infrx.feedback where feedback_id = %s", (row["feedback_id"],)
                          ).fetchone()
    assert stored == (C1, "console", "customer", cc.personal_org(conn, C1)), stored
    assert submit(conn)[1]["feedback_id"] == row["feedback_id"], "a replay stored twice"
    got = {"author_forged": submit(conn, key="k-2", author_role="operator")[0],
           "not_a_member": submit(conn, BOTH, key="k-3")[0],
           "unknown": door(conn, C1, "submit_feedback", {
               "request_id": l2.NOBODY, "name": "rating", "value": 4,
               "idempotency_key": "k-4"})[0],
           "malformed": door(conn, C1, "submit_feedback", {
               "request_id": "x", "name": "rating", "value": 4, "idempotency_key": "k-5"})[0]}
    assert got == {"author_forged": "invalid_request", "not_a_member": "not_found",
                   "unknown": "not_found", "malformed": "not_found"}, got
    assert submit(conn, None, key="k-6")[0] == "42501"
    cc.set_flag(conn, "feedback", False)
    assert submit(conn, key="k-7")[0] is not None, "stored with the flag off"
    return "server provenance; forged field refused; replay one row; non-member not_found"


@rolled_back
def check_review_is_l2s_rule_on_the_database_clock(conn) -> str:
    """LAB-ACCESS: a CURRENT developer+ member reads the customer signals on a request whose
    org's CURRENT grant names the model, `feedback` and `provider_sharing` - without the
    customer's principal, org or operator marker, and never a calibration label; a viewer is
    `forbidden`; another provider, a consumer-only user, a narrower, expired or revoked grant
    and an unknown request are all `not_found`."""
    submit(conn)
    ok(conn, "label_calibration", {
        "principal": "ops@infrx", "is_operator": True, "request_id": W["job"],
        "feedback_id": f"fb_{900:026d}", "label": "incorrect", "rubric_version": 2,
        "idem": {"org_id": cc.personal_org(conn, C1), "operation": "calibration.label",
                 "key": "cal-1", "payload_hash": "sha256:" + "cd" * 32}})
    status, rows = review(conn)
    assert status is None and [(r["name"], r["value"]) for r in rows] == [("rating", 4)], rows
    assert not {"author_principal", "org_id", "by_operator"} & set(rows[0]), rows[0]
    got = {"viewer": review(conn, VIEWER)[0],
           "other_provider": review(conn, BOTH)[0],
           "other_workspace": review(conn, BOTH, OTHER)[0],
           "consumer_only": review(conn, C1)[0],
           "unknown": review(conn, request=l2.NOBODY)[0]}
    share(conn, categories=["request_content"])
    got["narrower"] = review(conn)[0]
    share(conn, expires_at="2026-12-01T00:00:00Z")
    assert review(conn)[0] is None
    conn.execute("update infrx_test.clock set offset_s = offset_s + interval '400 days'")
    got["expired"] = review(conn)[0]
    conn.execute("update infrx_test.clock set offset_s = offset_s - interval '400 days'")
    share(conn)
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1,
                                         "grantor_org_id": l2.org(conn, C1),
                                         "recipient_provider_org_id": NEMO})
    got["revoked"] = review(conn)[0]
    assert got == {**dict.fromkeys(got, "not_found"), "viewer": "forbidden"}, got
    return "developer reads signals only; viewer forbidden; everything else not_found"


CHECKS = {c.__name__: c for c in (check_submit_takes_the_signal_and_derives_the_rest,
                                  check_review_is_l2s_rule_on_the_database_clock)}


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
def test_d6f_doors(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
