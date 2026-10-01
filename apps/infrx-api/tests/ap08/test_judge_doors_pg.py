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

from .conftest import LAB_PASSWORD, W, pg_reason
from .conftest import seed as seed  # noqa: PLC0414 - the world `code_mutants_d7.kill` seeds

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


def doors(conn) -> SessionDoors:
    """The doors on the Lab control unit's own login (`set_role=False`, as app.py)."""
    from psycopg.conninfo import make_conninfo

    from tests.d import pgharness
    lab = make_conninfo(pgharness.dsn(conn.info.dbname), user="infrx_lab_control",
                        password=LAB_PASSWORD)
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


def check_a_configuration_is_keyed_replayed_and_role_checked(conn) -> None:
    """08a: the Lab login configures as the session developer (0037's grant rule); the same
    key is the stored row (`replayed`), other values `idempotency_conflict`; a viewer and a
    developer of another provider are refused; the list answers it uncalibrated.
    Failure oracle: a second row per replay, a conflict accepted, a viewer configuring."""
    d = doors(conn)
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


def check_a_budget_change_is_keyed_and_administrator_only(conn) -> None:
    """08a: PUT budget = 0037's rule (administrator, own payer, PROVIDER_USD) with the key
    digest in the limit version; a replay answers that version, another limit conflicts.
    Failure oracle: a developer setting a budget, a replay writing a new version."""
    d = doors(conn)
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


def check_runs_are_listed_cancelled_and_results_follow_the_grant(conn) -> None:
    """08a/08d: a queued request lists with no ledger state; a cancel is recorded once and a
    reserved (`prepared`) run is released `cancelled` with its hold returned; results of a run
    whose judging grant was revoked are refused. Failure oracle: a cancel that leaves the hold,
    results readable after revocation, another provider seeing the run."""
    d = doors(conn)
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


def check_a_human_review_is_stored_once_with_its_provenance(conn) -> None:
    """08e: a developer's review of a request shared with the provider is stored once as
    provenance `human` by the session user; the same id + hash replays, another hash
    conflicts; a viewer is refused and an unshared request is not found; the read lists it.
    Failure oracle: a second row, a forged reviewer, a viewer writing, a foreign request."""
    d = doors(conn)
    job = W["job"]
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


def check_only_the_lab_login_gains_execute_and_the_flag_gates(conn) -> None:
    """0064's privileges: the Lab login executes the doors, a browser session (anon,
    authenticated) gains none; the `lab_submission` flag off is a typed 503.
    Failure oracle: authenticated reaching a 0064 door over PostgREST, a flag-off write."""
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
    assert refused(doors(conn).call(DEV, "lab_judge_config_list", NEMO, None, None, 26)) == \
        "DependencyUnavailable"


def check_the_http_family_composes_on_the_lab_login(conn) -> None:
    """08a/08e end to end: `/lab/v1/judge/*` and the review routes over the real doors on the
    Lab login - configure (keyed), budget (administrator), estimate (a report), run (202,
    identity from the key, replayed), cancel, a human review and its read; a viewer reads no
    run. Failure oracle: any route answering from anything but the session user's doors."""
    import types

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from infrx.contracts import api
    from infrx.gateway import control
    from infrx.gateway.routes import lab_judge, lab_reviews
    from infrx.lab.judge_api.service import JudgeApi

    def client(user: str) -> TestClient:
        rt = types.SimpleNamespace(actors=control.StaticActors(api.Actor(audience="session",
                                                                         user_id=user)),
                                   lab_judge=JudgeApi(doors(conn)))
        app = FastAPI()
        lab_judge.register(app, rt)
        lab_reviews.register(app, rt)
        return TestClient(app, raise_server_exceptions=False)

    q, judge, dev = {"provider_org_id": NEMO}, lab_judge.PREFIX, client(DEV)
    body = {"grantor_org_id": l2.org(conn, C1), "model_id": l2.MODEL, "judge_model": "judge-1",
            "rubric_version": 1, "sample_size": 20}
    made = dev.post(f"{judge}/configs", params=q, headers={"Idempotency-Key": "cfg"}, json=body)
    assert made.status_code == 201, made.text
    config = made.json()["config_id"]
    assert dev.post(f"{judge}/configs", params=q, headers={"Idempotency-Key": "cfg"},
                    json=body).json()["config_id"] == config
    budget = client(ADMIN).put(f"{judge}/budgets/{j.PAYER}", params=q,
                               headers={"Idempotency-Key": "b"},
                               json={"limit": {"amount": "75.5", "unit": "PROVIDER_USD"}})
    assert budget.status_code == 200, budget.text
    assert budget.json()["limit"] == {"amount": "75.50000000", "unit": "PROVIDER_USD"}
    assert dev.put(f"{judge}/budgets/{j.PAYER}", params=q, headers={"Idempotency-Key": "b2"},
                   json={"limit": {"amount": "1", "unit": "PROVIDER_USD"}}).status_code == 403
    estimate = dev.post(f"{judge}/estimates", params=q, json={"config_id": config}).json()
    assert (estimate["priced"], estimate["authorizes_spend"]) == (False, False)
    run = {"config_id": config, "payer_ref": j.PAYER}
    queued = dev.post(f"{judge}/runs", params=q, headers={"Idempotency-Key": "run"}, json=run)
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["operation_id"]
    assert dev.post(f"{judge}/runs", params=q, headers={"Idempotency-Key": "run"},
                    json=run).json()["operation_id"] == run_id
    assert conn.execute("select count(*) from infrx.lab_judge_requests").fetchone()[0] == 1
    assert dev.get(f"{judge}/runs/{run_id}", params=q).json()["domain_state"] == "queued"
    assert dev.get(f"{judge}/runs/{run_id}/results", params=q).json()["data"] == []
    assert client(VIEWER).get(f"{judge}/runs", params=q).status_code == 403
    cancelled = dev.post(f"{judge}/runs/{run_id}/cancel", params=q,
                         headers={"Idempotency-Key": "x"}).json()
    assert cancelled["domain_state"] == "cancelled" and cancelled["cancel_requested"]
    path = f"/lab/v1/traces/{W['job']}"
    review = dev.post(f"{path}/reviews", params=q, headers={"Idempotency-Key": "r"},
                      json={"verdict": "unsure", "run_id": run_id})
    assert review.status_code == 201, review.text
    assert (review.json()["provenance"], review.json()["reviewer"]) == ("human", DEV)
    shown = dev.get(f"{path}/feedback", params=q).json()
    assert [r["review_id"] for r in shown["reviews"]] == [review.json()["review_id"]]


CHECKS = {c.__name__: c for c in (
    check_the_http_family_composes_on_the_lab_login,
    check_a_configuration_is_keyed_replayed_and_role_checked,
    check_a_budget_change_is_keyed_and_administrator_only,
    check_runs_are_listed_cancelled_and_results_follow_the_grant,
    check_a_human_review_is_stored_once_with_its_provenance,
    check_only_the_lab_login_gains_execute_and_the_flag_gates)}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_ap08_doors(pg, name) -> None:
    CHECKS[name](pg)
