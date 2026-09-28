#!/usr/bin/env python3
"""D6J / SR-C3L-1 (wave-5 LW2, lab-sql; the judge lane's C3L request): the four authenticated
judge doors of `0037_lab_judge_doors.sql` on real PostgreSQL, called as each member of the
role matrix with its own session - LAB-ACCESS and JUDGE-BUDGET (the SQL half of C3L's
expired-grant, forged-org/model and double-click properties).

World: test_d6j_judge's (C1's external_judging grant to NEMO over request and response
content, the flag on, NEMO's payer budget) - 2 consumers (C1, BOTH), 2 providers (NEMO: DEV
developer, ADMIN administrator, VIEWER viewer; OTHER: BOTH developer), BOTH in both products.
Each `check_*` is the check a mutant in `code_mutants_d6j.py` (`DOORS`) must break.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d6j_doors.py
"""
from __future__ import annotations

import threading

import psycopg
import pytest
from psycopg.types.json import Jsonb

from infrx.state import migrations

from . import checks
from . import checks_credit as cc
from . import pgharness
from . import test_d6j_judge as j

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d6jd"

l2, NEMO, OTHER, C1, BOTH = j.l2, j.NEMO, j.OTHER, j.C1, j.BOTH
DEV, ADMIN, VIEWER = l2.DEV, l2.ADMIN, l2.VIEWER
call, ok, rolled_back = j.call, j.ok, j.rolled_back
SIGNATURES = {"lab_judge_configure": "uuid, uuid, uuid, text, int, int",
              "lab_judge_set_budget": "uuid, text, jsonb",
              "lab_judge_request_run": "uuid, uuid, uuid, text",
              "lab_judge_calibration": "uuid, uuid, int"}
RUN = "7a000000-0000-4000-8000-00000000007a"
seed = j.seed


def door(conn, user: str | None, fn: str, *args):
    """(SQLSTATE or None, answer) of one door call as `user` (None: anon); its writes stay in
    the check's transaction."""
    casts = SIGNATURES[fn].split(", ")
    sql = f"select public.{fn}({', '.join(f'%s::{c}' for c in casts)})"
    try:
        with conn.transaction():
            conn.execute(checks._jwt(user) if user else checks.SESSIONS["anon"])
            answer = conn.execute(sql, [Jsonb(a) if isinstance(a, dict) else a
                                        for a in args]).fetchone()[0]
            conn.execute("reset role")
    except psycopg.Error as refused:
        return refused.sqlstate, None
    return None, answer


def configure(conn, user: str = DEV, *, provider: str = NEMO, grantor: str | None = None,
              model: str = l2.MODEL, samples: int = 20):
    return door(conn, user, "lab_judge_configure", provider, grantor or l2.org(conn, C1),
                model, "judge-1", 1, samples)


def request(conn, config: str, user: str = DEV, *, provider: str = NEMO, run: str = RUN,
            payer: str = j.PAYER):
    return door(conn, user, "lab_judge_request_run", provider, run, config, payer)


def revoke(conn) -> None:
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": l2.org(conn, C1),
                                         "recipient_provider_org_id": NEMO})


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_only_current_members_at_the_right_role_pass_a_door(conn) -> str:
    """LAB-ACCESS: anon cannot execute a door; a consumer-only user, a member of ANOTHER
    provider (BOTH, who is also a consumer), a viewer (every door) and a developer (the
    budget door) are 42501; a revoked membership is 42501; with the flag off every door is
    55000 - the launched App's database answers nothing until the Lab is enabled."""
    cfg = configure(conn)[1]["config_id"]
    budget = (NEMO, j.PAYER, {"unit": "PROVIDER_USD", "value": "50.00000000"})
    calls = {"configure": lambda u: configure(conn, u),
             "budget": lambda u: door(conn, u, "lab_judge_set_budget", *budget),
             "request": lambda u: request(conn, cfg, u),
             "calibration": lambda u: door(conn, u, "lab_judge_calibration", NEMO, None, 10)}
    got = {(name, who): fn(user)[0] for name, fn in calls.items()
           for who, user in (("consumer_only", C1), ("other_provider", BOTH),
                             ("viewer", VIEWER))}
    got[("budget", "developer")] = calls["budget"](DEV)[0]
    assert got == dict.fromkeys(got, "42501"), got
    assert {name: fn(None)[0] for name, fn in calls.items()} == dict.fromkeys(calls, "42501")
    assert calls["budget"](ADMIN)[0] is None and calls["calibration"](DEV)[0] is None
    conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                 "where provider_org_id = %s and user_id = %s", (NEMO, DEV))
    assert calls["request"](DEV)[0] == "42501", "a revoked member queued a run"
    cc.set_flag(conn, "lab_submission", False)
    off = {name: fn(ADMIN)[0] for name, fn in calls.items()}
    assert off == dict.fromkeys(calls, "55000"), off
    return "consumer-only, other provider, viewer, revoked, anon: 42501; flag off: 55000"


@rolled_back
def check_a_configuration_needs_the_grantors_current_judging_grant(conn) -> str:
    """LAB-ACCESS (C3L's forged org/model and expired grant, SQL half): a configuration names
    a grantor whose CURRENT grant to this provider names the model, over the question and
    the answer, for external judging - another org, another model, a narrower, other-purpose,
    expired or revoked grant is 42501; a bad size is 22023; the row is the caller's."""
    status, row = configure(conn)
    assert status is None and row["sample_size"] == 20, (status, row)
    got = {"other_org": configure(conn, grantor=l2.org(conn, BOTH))[0],
           "other_model": configure(conn, model=l2.OTHER_MODEL)[0],
           "other_provider_workspace": configure(conn, BOTH, provider=OTHER)[0]}
    j.judge_grant(conn, categories=["request_content"])
    got["question_only"] = configure(conn)[0]
    j.judge_grant(conn, purposes=["training"])
    got["training"] = configure(conn)[0]
    j.judge_grant(conn, expires_at="2026-12-01T00:00:00Z")
    assert configure(conn)[0] is None
    conn.execute("update infrx_test.clock set offset_s = offset_s + interval '400 days'")
    got["expired"] = configure(conn)[0]
    conn.execute("update infrx_test.clock set offset_s = offset_s - interval '400 days'")
    j.judge_grant(conn)
    revoke(conn)
    got["revoked"] = configure(conn)[0]
    assert got == dict.fromkeys(got, "42501"), got
    j.judge_grant(conn)
    assert configure(conn, samples=201)[0] == "22023"
    creator = conn.execute("select distinct created_by::text from infrx.lab_judge_configs"
                           ).fetchall()
    assert creator == [(DEV,)], creator
    return "other org/model/workspace, narrower, training, expired, revoked: 42501"


@rolled_back
def check_the_budget_door_writes_the_administrators_own_payer(conn) -> str:
    """JUDGE-BUDGET (R159): the administrator sets a PROVIDER_USD limit for a payer of THIS
    provider only, through D6J's one budget row (a versioned limit naming the user);
    another provider's payer is 42501, another unit 22023."""
    status, row = door(conn, ADMIN, "lab_judge_set_budget", NEMO, j.PAYER,
                       {"unit": "PROVIDER_USD", "value": "250.00000000"})
    assert status is None and row["limit"] == "250.00000000", (status, row)
    actor = conn.execute("select actor from infrx.lab_budget_limits where payer_ref = %s "
                         "order by version desc limit 1", (j.PAYER,)).fetchone()[0]
    assert actor == ADMIN, actor
    assert door(conn, ADMIN, "lab_judge_set_budget", NEMO, j.OTHER_PAYER,
                {"unit": "PROVIDER_USD", "value": "1"})[0] == "42501"
    assert door(conn, ADMIN, "lab_judge_set_budget", NEMO, j.PAYER,
                {"unit": "CREDIT", "value": "1"})[0] == "22023"
    return "own payer, PROVIDER_USD, versioned under the administrator"


@rolled_back
def check_a_run_request_is_idempotent_and_foreign_ids_are_unknown(conn) -> str:
    """C3L's double click: the same run id is the same request; another provider's run id,
    or another provider's configuration, is P0002 (unknown); a payer of another provider or
    a grant revoked since the configuration is 42501."""
    cfg = configure(conn)[1]["config_id"]
    first = request(conn, cfg)
    assert first[0] is None and request(conn, cfg) == first, "a double click queued twice"
    count = conn.execute("select count(*) from infrx.lab_judge_requests").fetchone()[0]
    assert count == 1, count
    ok(conn, "lab_put_access_grant", l2.scope(conn, owner=BOTH, provider=OTHER,
                                              model_ids=[l2.OTHER_MODEL],
                                              purposes=["external_judging"],
                                              categories=j.BOTH_CATEGORIES))
    other_cfg = configure(conn, BOTH, provider=OTHER, grantor=l2.org(conn, BOTH),
                          model=l2.OTHER_MODEL)[1]["config_id"]
    got = {"foreign_run": request(conn, other_cfg, BOTH, provider=OTHER,
                                  payer=j.OTHER_PAYER)[0],
           "foreign_config": request(conn, other_cfg, run=j.uid(0x7b))[0]}
    assert got == dict.fromkeys(got, "P0002"), got
    assert request(conn, cfg, run=j.uid(0x7c), payer=j.OTHER_PAYER)[0] == "42501"
    revoke(conn)
    assert request(conn, cfg, run=j.uid(0x7d))[0] == "42501", "queued after revocation"
    return "double click one request; foreign run/config P0002; foreign payer, revoked 42501"


@rolled_back
def check_calibration_is_the_providers_own_labels_in_bounded_pages(conn) -> str:
    """LAB-ACCESS / C3L paging: the calibration read is this provider's runs' labels only,
    keyset on label_id, never more than 50 rows."""
    j.submitted(conn, j.uid(0x60))
    ok(conn, "lab_judge_record_results", {"run_id": j.uid(0x60), "results": [
        {"sample_id": s, "rubric_version": v, "accepted": True, "result": {"s": v}}
        for s in j.SAMPLES for v in (1, 2)]})
    other = ok(conn, "lab_judge_reserve", j.reserve(j.uid(0x61), provider=OTHER,
                                                    payer=j.OTHER_PAYER,
                                                    grant=j.W["other_grant"]))
    ok(conn, "lab_judge_begin_submit", {"run_id": other["run_id"]})
    ok(conn, "lab_judge_record_sent", {"run_id": other["run_id"], "sample_ids": j.SAMPLES})
    ok(conn, "lab_judge_record_submission", {"run_id": other["run_id"], "external_id": "o"})
    ok(conn, "lab_judge_record_results", {"run_id": other["run_id"], "results": [
        {"sample_id": j.SAMPLES[0], "rubric_version": 1, "accepted": False, "result": {}}]})
    page = door(conn, DEV, "lab_judge_calibration", NEMO, None, 4)[1]
    rest = door(conn, DEV, "lab_judge_calibration", NEMO, page[-1]["label_id"], 50)[1]
    labels = [r["label_id"] for r in page + rest]
    assert (len(page), len(rest), labels == sorted(labels)) == (4, 2, True), (page, rest)
    assert {r["run_id"] for r in page + rest} == {j.uid(0x60)}, "another provider's labels"
    assert len(door(conn, DEV, "lab_judge_calibration", NEMO, None, 999)[1]) == 6
    conn.execute("insert into infrx.lab_judge_results (run_id, sample_id, rubric_version, "
                 "accepted, result) select %s, %s, v, true, '{}' from "
                 "generate_series(3, 60) v", (j.uid(0x60), j.SAMPLES[0]))
    assert len(door(conn, DEV, "lab_judge_calibration", NEMO, None, 999)[1]) == 50
    return "own labels only; keyset pages; clamped to 50"


def check_a_concurrent_double_click_queues_one_run(conn) -> str:
    """C3L's double click at the same instant (commits): two sessions of the developer queue
    one run id together - both are answered the one stored request."""
    with conn.transaction():
        cfg = configure(conn)[1]["config_id"]
    run = j.uid(0x7e)
    gate, answers = threading.Barrier(2), [None, None]

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            with mine.transaction():
                answers[i] = request(mine, cfg, run=run)
    threads = [threading.Thread(target=one, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert answers[0] == answers[1] and answers[0][0] is None, answers
    rows = conn.execute("select count(*) from infrx.lab_judge_requests where run_id = %s",
                        (run,)).fetchone()[0]
    assert rows == 1, rows
    return "two racing clicks: one request, the same answer"


CHECKS = {c.__name__: c for c in (
    check_only_current_members_at_the_right_role_pass_a_door,
    check_a_configuration_needs_the_grantors_current_judging_grant,
    check_the_budget_door_writes_the_administrators_own_payer,
    check_a_run_request_is_idempotent_and_foreign_ids_are_unknown,
    check_calibration_is_the_providers_own_labels_in_bounded_pages,
    check_a_concurrent_double_click_queues_one_run)}


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
def test_d6j_doors(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
