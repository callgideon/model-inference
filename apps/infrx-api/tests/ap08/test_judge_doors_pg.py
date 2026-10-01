"""AP-08 08a/08e on real PostgreSQL (key ap8): the judge and review doors run by
`infrx.lab.judge_api.doors.SessionDoors` on the Lab control unit's own login, as the session
user the ActorSource verified - 0037/0038's authority unchanged, 0064's reads and keyed writes.

    INFRX_D_TASK=ap8 uv run --frozen pytest -q tests/ap08/test_judge_doors_pg.py
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.contracts import errors
from infrx.lab.judge_api.doors import SessionDoors
from infrx.state.jobstore import connector
from tests.d import test_d6j_doors as jd
from tests.d import test_d6j_judge as j
from tests.d import test_l2sql_access as l2

from .conftest import pg_reason

pytestmark = pytest.mark.skipif(pg_reason() is not None, reason=f"{pg_reason()}")

NEMO, OTHER, C1 = j.NEMO, j.OTHER, j.C1
DEV, ADMIN, VIEWER = l2.DEV, l2.ADMIN, l2.VIEWER
CONFIG = "0c000000-0000-4000-8000-0000000000c1"
RUN = "7a000000-0000-4000-8000-0000000000a1"
REVIEW = "4e000000-0000-4000-8000-0000000000e1"
DIGEST = "d" * 64
HASH = "sha256:" + "e" * 64


def run(coro):
    return asyncio.run(coro)


def doors(lab: str) -> SessionDoors:
    return SessionDoors(connector(lab, set_role=False))


def configure(d: SessionDoors, conn, user: str = DEV, *, config: str = CONFIG,
              samples: int = 20):
    return run(d.call(user, "lab_judge_configure_keyed", NEMO, config, l2.org(conn, C1),
                      l2.MODEL, "judge-1", 1, samples))


def refused(coro) -> str:
    try:
        run(coro)
    except errors.DomainError as exc:
        return type(exc).__name__
    return "allowed"


def test_ap08_doors__a_configuration_is_keyed_replayed_and_role_checked(pg):
    """08a: the Lab login configures as the session developer (0037's grant rule); the same
    key is the stored row (`replayed`), other values `idempotency_conflict`; a viewer and a
    developer of another provider are refused; the list answers it uncalibrated.
    Failure oracle: a second row per replay, a conflict accepted, a viewer configuring."""
    conn, lab, _ = pg
    d = doors(lab)
    first = configure(d, conn)
    assert (first["config_id"], first["replayed"]) == (CONFIG, False)
    assert configure(d, conn)["replayed"] is True
    assert refused(d.call(DEV, "lab_judge_configure_keyed", NEMO, CONFIG, l2.org(conn, C1),
                          l2.MODEL, "judge-1", 1, 21)) == "IdempotencyConflict"
    assert refused(d.call(VIEWER, "lab_judge_configure_keyed", NEMO,
                          "0c000000-0000-4000-8000-0000000000c2", l2.org(conn, C1), l2.MODEL,
                          "judge-1", 1, 20)) == "Forbidden"
    assert refused(d.call(j.BOTH, "lab_judge_config_list", NEMO, None, None, 26)) == "Forbidden"
    listed = run(d.call(DEV, "lab_judge_config_list", NEMO, None, None, 26))
    assert [c["config_id"] for c in listed] == [CONFIG]
    assert listed[0]["calibration"]["state"] == "uncalibrated"
    assert conn.execute("select count(*) from infrx.lab_judge_configs").fetchone()[0] == 1


def test_ap08_doors__a_budget_change_is_keyed_and_administrator_only(pg):
    """08a: PUT budget = 0037's rule (administrator, own payer, PROVIDER_USD) with the key
    digest in the limit version; a replay answers that version, another limit conflicts.
    Failure oracle: a developer setting a budget, a replay writing a new version."""
    conn, lab, _ = pg
    d = doors(lab)
    limit = {"unit": "PROVIDER_USD", "value": "50.00000000"}
    first = run(d.call(ADMIN, "lab_judge_set_budget_keyed", NEMO, j.PAYER, limit, DIGEST))
    again = run(d.call(ADMIN, "lab_judge_set_budget_keyed", NEMO, j.PAYER, limit, DIGEST))
    assert (first["replayed"], again["replayed"]) == (False, True)
    assert first["version"] == again["version"] and again["limit"] == "50.00000000"
    assert refused(d.call(ADMIN, "lab_judge_set_budget_keyed", NEMO, j.PAYER,
                          {**limit, "value": "60"}, DIGEST)) == "IdempotencyConflict"
    assert refused(d.call(DEV, "lab_judge_set_budget_keyed", NEMO, j.PAYER, limit,
                          "f" * 64)) == "Forbidden"
    assert refused(d.call(ADMIN, "lab_judge_set_budget_keyed", NEMO, j.PAYER,
                          {"unit": "CREDIT", "value": "5"}, "f" * 64)) == "InvalidRequest"
    budgets = run(d.call(DEV, "lab_judge_budget_list", NEMO))
    assert [(b["payer_ref"], b["limit"], b["version"]) for b in budgets] == \
        [(j.PAYER, "50.00000000", first["version"])]


def test_ap08_doors__runs_are_listed_cancelled_and_results_follow_the_grant(pg):
    """08a/08d: a queued request lists with no ledger state; a cancel is recorded once and a
    reserved (`prepared`) run is released `cancelled` with its hold returned; results of a run
    whose judging grant was revoked are refused. Failure oracle: a cancel that leaves the hold,
    results readable after revocation, another provider seeing the run."""
    conn, lab, _ = pg
    d = doors(lab)
    configure(d, conn)
    queued = run(d.call(DEV, "lab_judge_request_run", NEMO, RUN, CONFIG, j.PAYER))
    assert queued["run_id"] == RUN
    (row,) = run(d.call(DEV, "lab_judge_run_list", NEMO, RUN, None, 26))
    assert (row["ledger_state"], row["cancel_requested_at"]) == (None, None)
    assert run(d.call(j.BOTH, "lab_judge_run_list", OTHER, None, None, 26)) == []
    assert refused(d.call(j.BOTH, "lab_judge_cancel", OTHER, RUN)) == "NotFound"
    l2.ok(conn, "lab_judge_reserve", j.reserve(RUN))
    assert j.held(conn) == ("40.00000000", "0.00000000")
    cancelled = run(d.call(DEV, "lab_judge_cancel", NEMO, RUN))
    assert cancelled["ledger_state"] == "cancelled" and cancelled["cancel_requested_at"]
    assert j.held(conn) == ("0.00000000", "0.00000000")
    assert run(d.call(DEV, "lab_judge_cancel", NEMO, RUN))["ledger_state"] == "cancelled"
    assert run(d.call(DEV, "lab_judge_run_results", NEMO, RUN, None, 26)) == []
    jd.revoke(conn)
    assert refused(d.call(DEV, "lab_judge_run_results", NEMO, RUN, None, 26)) == "Forbidden"


def test_ap08_doors__a_human_review_is_stored_once_with_its_provenance(pg):
    """08e: a developer's review of a request shared with the provider is stored once as
    provenance `human` by the session user; the same id + hash replays, another hash
    conflicts; a viewer is refused and an unshared request is not found; the read lists it.
    Failure oracle: a second row, a forged reviewer, a viewer writing, a foreign request."""
    conn, lab, job = pg
    d = doors(lab)
    body = {"provider_org_id": NEMO, "request_id": job, "review_id": REVIEW,
            "input_hash": HASH, "verdict": "fail", "comment": "missed step 3"}
    first = run(d.call(DEV, "lab_trace_review", body))
    assert (first["provenance"], first["reviewer"], first["replayed"]) == ("human", DEV, False)
    assert run(d.call(DEV, "lab_trace_review", body))["replayed"] is True
    assert refused(d.call(DEV, "lab_trace_review", {**body, "input_hash": "sha256:" + "0" * 64})
                   ) == "IdempotencyConflict"
    assert refused(d.call(VIEWER, "lab_trace_review", {**body, "review_id": RUN})) == \
        "Forbidden"
    assert refused(d.call(DEV, "lab_trace_review", {**body, "review_id": RUN,
                                                     "request_id": l2.NOBODY})) == "NotFound"
    assert refused(d.call(DEV, "lab_trace_review", {**body, "reviewer": VIEWER})) == \
        "InvalidRequest"
    listed = run(d.call(DEV, "lab_trace_reviews", {"provider_org_id": NEMO, "request_id": job}))
    assert [(r["review_id"], r["verdict"]) for r in listed] == [(REVIEW, "fail")]
    assert run(d.call(DEV, "lab_review_feedback", {"provider_org_id": NEMO,
                                                    "request_id": job})) == []


def test_ap08_doors__only_the_lab_login_gains_execute_and_the_flag_gates(pg):
    """0064's privileges: the Lab login executes the doors, a browser session (anon,
    authenticated) gains none; the `lab_submission` flag off is a typed 503.
    Failure oracle: authenticated reaching a 0064 door over PostgREST, a flag-off write."""
    conn, lab, _ = pg
    doors_0064 = ("lab_judge_configure_keyed(uuid, uuid, uuid, uuid, text, int, int)",
                  "lab_judge_config_list(uuid, uuid, uuid, int)",
                  "lab_judge_set_budget_keyed(uuid, text, jsonb, text)",
                  "lab_judge_budget_list(uuid)", "lab_judge_run_list(uuid, uuid, uuid, int)",
                  "lab_judge_run_results(uuid, uuid, uuid, int)",
                  "lab_judge_cancel(uuid, uuid)", "lab_trace_review(jsonb)",
                  "lab_trace_reviews(jsonb)")
    for role, expected in (("infrx_lab_control", True), ("authenticated", False),
                           ("anon", False)):
        got = {fn: conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                (role, f"public.{fn}")).fetchone()[0] for fn in doors_0064}
        assert set(got.values()) == {expected}, (role, got)
    conn.execute("update infrx.feature_flags set enabled = false where name = 'lab_submission'")
    assert refused(doors(lab).call(DEV, "lab_judge_config_list", NEMO, None, None, 26)) == \
        "DependencyUnavailable"
