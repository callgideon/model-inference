"""AP-08 (api-judge-2) on real PostgreSQL (key ap8): SR-AP08-1's rubric store through
`JudgeApi` on the Lab control unit's own login, as the session user - a platform operator
stores a reviewed definition once per version, a member reads the versions, the judge worker
reads a run's pinned rubric. The SR mutants (`mutants.SR_MUTANTS`) are killed by these checks.

    INFRX_D_TASK=ap8 uv run --frozen pytest -q tests/ap08/test_rubric_store_pg.py
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest

from infrx.contracts import errors
from infrx.judge import rubric as r
from infrx.judge import start
from infrx.lab.judge_api import rubric as wire
from infrx.lab.judge_api.service import ConfigBody, JudgeApi
from infrx.state.jobstore import connector
from tests.d import checks_operator as co
from tests.d import pgharness
from tests.d import test_d6j_judge as j
from tests.d import test_l2sql_access as l2

from .conftest import pg_reason
from .conftest import seed as seed  # noqa: PLC0414 - the world `code_mutants_d7.kill` seeds
from .test_judge_doors_pg import doors
from .test_rubric_versions import sop_body

pytestmark = pytest.mark.skipif(pg_reason() is not None, reason=f"{pg_reason()}")

NEMO = j.NEMO


def run(coro):
    return asyncio.run(coro)


def refused(coro) -> str:
    try:
        run(coro)
    except errors.DomainError as exc:
        return type(exc).__name__
    return "allowed"


def store(conn, user: str, key: str = "rb-1", **over):
    return JudgeApi(doors(conn)).create_rubric(user, NEMO, key,
                                               wire.RubricBody.model_validate(sop_body(**over)))


def check_a_rubric_version_is_stored_once_by_an_operator(conn) -> None:
    """Only a platform operator stores a version; the same definition again is the stored row,
    another one for the version 409; the row cannot be edited; a member lists it, a stranger
    cannot; no browser role executes the doors. Failure oracle: a provider developer writing
    a rubric everyone's judge grades with, a version overwritten in place, a stored version
    leaking to a non-member."""
    op = co.make_operator(conn)
    first = run(store(conn, op))
    assert (first.version, first.state, first.sop_steps) == (2, "active", tuple(sop_body()["sop_steps"]))
    assert run(store(conn, op, "rb-2")).digest == first.digest
    assert refused(store(conn, op, "rb-3", sop_steps=["Open the cabinet"])) == \
        "IdempotencyConflict"
    assert refused(store(conn, l2.DEV, "rb-4", version=3)) == "Forbidden"
    assert conn.execute("select count(*) from infrx.lab_judge_rubrics").fetchone()[0] == 1
    try:
        with conn.transaction():
            conn.execute("update infrx.lab_judge_rubrics set review_ref = 'edited'")
        edited = True
    except psycopg.Error:
        edited = False
    assert not edited, "a stored rubric version was edited in place"
    listed = run(JudgeApi(doors(conn)).rubrics(l2.DEV, NEMO)).data
    assert {d.version: d.state for d in listed} == {1: "active", 2: "active"}
    assert refused(JudgeApi(doors(conn)).rubrics(j.BOTH, NEMO)) == "Forbidden"
    for role in ("anon", "authenticated"):
        assert not conn.execute(
            "select has_function_privilege(%s, 'public.lab_judge_rubric_create(jsonb)', "
            "'execute')", (role,)).fetchone()[0], role


def check_a_configuration_pins_a_stored_version_and_the_worker_reads_it(conn) -> None:
    """Before the SOP definition is stored a configuration of v2 is refused; after, it is
    accepted and the worker's read of a run of it answers the stored definition, a run of a
    code version the code rubric, an unknown run nothing. Failure oracle: a v2 run graded
    with v1's criteria (or with nothing), a run of another configuration's version."""
    api = JudgeApi(doors(conn))
    grantor = l2.org(conn, j.C1)

    def configure(key: str, version: int):
        return api.configure(l2.DEV, NEMO, key, ConfigBody(
            grantor_org_id=grantor, model_id=l2.MODEL, judge_model="judge-1",
            rubric_version=version, sample_size=2))

    assert refused(configure("c-2", 2)) == "StateConflict"
    run(store(conn, co.make_operator(conn)))
    sop = run(configure("c-2", 2))
    v1 = run(configure("c-1", 1))
    rubric_of = start.pg_rubric_of(connector(pgharness.dsn(conn.info.dbname)))
    runs = {}
    for config, n in ((sop, 1), (v1, 2)):
        runs[config.rubric_version] = f"7a000000-0000-4000-8000-0000000003{n:02x}"
        run(doors(conn).call(l2.DEV, "lab_judge_request_run", NEMO,
                             runs[config.rubric_version], config.config_id, j.PAYER))
    graded = run(rubric_of(runs[2]))
    assert graded is not None and (graded.rubric_id, graded.sop_steps) == \
        ("sop-video", tuple(sop_body()["sop_steps"]))
    assert run(rubric_of(runs[1])) is r.MARLIN_VIDEO_V1
    assert run(rubric_of("7a000000-0000-4000-8000-0000000003ff")) is None


CHECKS = {c.__name__: c for c in (check_a_rubric_version_is_stored_once_by_an_operator,
                                  check_a_configuration_pins_a_stored_version_and_the_worker_reads_it)}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_ap08_rubric_store(pg, name) -> None:
    CHECKS[name](pg)
