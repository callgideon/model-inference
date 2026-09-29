#!/usr/bin/env python3
"""lab-sql LW7: `0055_lab_variants_requeue.sql` on real PostgreSQL - R3's optimization-variant
listing (WR-C6-VARIANTS) and the failed-import requeue (WR-C6-REQUEUE), composed with their
stores.

World: test_d7_variant's (NEMO's variants and B2 reports, OTHER beside it). Each `check_*` is
the check a mutant in `tests/d/test_code_mutants_lw7.py` must break.

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw7.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.state import migrations
from infrx.state.jobstore import connector
from psycopg.types.json import Jsonb

from ..d import checks_credit as cc
from ..d import pgharness
from ..d import test_d7_variant as v

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lw7"
NEMO, OTHER, uid, ok, refusal, t = v.NEMO, v.OTHER, v.uid, v.ok, v.refusal, v.t
RPCS = ("lab_optimization_variants", "lab_import_requeue")
SPEC = {"spec": {"import_id": "x"}, "accept_rejects": False, "actor": "dev@nemo"}
seed = v.seed


def failed(conn, tag: int, provider: str = NEMO) -> str:
    """An import job of `provider` enqueued, claimed and finished `failed`; its id."""
    job = uid(1, tag)
    ok(conn, "lab_import_job_enqueue", {"job_id": job, "provider_org_id": provider,
                                        "spec": SPEC, "actor": "dev@nemo"})
    ok(conn, "lab_import_job_claim", {"worker_id": "i5", "limit": 10, "redelivery_s": 30})
    ok(conn, "lab_import_job_finish", {"job_id": job, "worker_id": "i5", "state": "failed",
                                       "error": "the source is gone"})
    return job


def requeue(job: str, new: str, provider: str = NEMO, actor: str | None = "dev2@nemo"):
    return {"job_id": job, "new_job_id": new, "provider_org_id": provider, "actor": actor}


# ----------------------------------------------------------------------------- checks
@v.rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session executes 0055's functions; the platform role does, and so
    does the control unit's login (`infrx_lab_control`, R237: the box's `/lab/v1/*` server
    composes lab_datasets and lab_releases on it - fix round LW7-SCOPE-1)."""
    probes = [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached 0055: {reached}"
    got = cc.refused_as(conn, "service", "select infrx.lab_optimization_variants("
                        f"'{{\"provider_org_id\": \"{NEMO}\"}}')")
    assert got is None, f"the platform role cannot read: {got}"
    unit = {name: conn.execute("select has_function_privilege('infrx_lab_control', "
                               f"'infrx.{name}(jsonb)', 'execute')").fetchone()[0]
            for name in RPCS}
    assert unit == dict.fromkeys(RPCS, True), f"the control unit cannot execute: {unit}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused"


def check_variants_are_the_providers_own_with_their_latest_comparison(conn) -> str:
    """WR-C6-VARIANTS / R227: the provider's `lab:variant` records, oldest first, each with
    both serving identities, what changed, and its NEWEST stored comparison (outcome, reasons,
    report digest, claim) or null; another provider's variants never appear, an unknown
    provider reads []. Commits (the store reads on its own connection; tags 0x71-0x73)."""
    report = v.stored_report(conn, 0x71)
    first = v.variant(conn, 0x71)
    t.advance(conn, 5)                                      # the seed's clock is frozen
    second = v.variant(conn, 0x72)
    foreign = v.variant(conn, 0x73, OTHER)
    older = ok(conn, "lab_put_variant_comparison", v.put(v.comparison(
        first, report, "inconclusive", False)))["comparison_digest"]
    t.advance(conn, 5)
    newest = v.comparison(first, report, "equivalent", True)
    latest = ok(conn, "lab_put_variant_comparison", v.put(newest))["comparison_digest"]
    assert older != latest
    rows = ok(conn, "lab_optimization_variants", {"provider_org_id": NEMO})
    assert [r["variant_ref"] for r in rows] == [first, second], rows       # never `foreign`
    serving = f"lab:serving:{NEMO}:{{}}@sha256:{'e' * 64}"
    assert {k: rows[0].get(k) for k in ("base_serving_ref", "variant_serving_ref", "changes")} == {
        "base_serving_ref": serving.format(uid(2, 0x71)),
        "variant_serving_ref": serving.format(uid(3, 0x71)), "changes": ["fp8 weights"]}
    assert rows[0].get("comparison") == {**newest, "comparison_digest": latest}, rows[0]
    assert rows[1].get("comparison") is None, rows[1]
    assert [r["variant_ref"] for r in ok(conn, "lab_optimization_variants",
                                         {"provider_org_id": OTHER})] == [foreign]
    assert ok(conn, "lab_optimization_variants", {"provider_org_id": uid(99, 0x99)}) == []
    from infrx.state.lab_variants import PgLabVariants
    store = PgLabVariants(connector(pgharness.dsn(conn.info.dbname)))
    assert asyncio.run(store.variants(NEMO)) == rows
    return "own provider only, oldest first, newest comparison or null; the store reads it"


@v.rolled_back
def check_a_failed_import_is_requeued_as_a_new_job_naming_its_predecessor(conn) -> str:
    """WR-C6-REQUEUE / R243: a failed job is requeued as a NEW queued job (the requeuer its
    creator and actor, the same import spec) that records its predecessor; the failed job
    stays terminal; a replay answers the same successor (never a second); the datasets role
    claims the successor unchanged; another provider's or an unknown id is `not_found`."""
    job = failed(conn, 0x81)
    again = ok(conn, "lab_import_requeue", requeue(job, uid(2, 0x81)))
    assert [again.get(k) for k in ("job_id", "state", "requeued_from", "attempts",
                                   "provider_org_id")] == [uid(2, 0x81), "queued", job, 0, NEMO]
    assert again.get("spec") == {**SPEC, "actor": "dev2@nemo"}, again
    assert t.count(conn, "select count(*) from infrx.lab_import_jobs where job_id = %s and "
                   "created_by = 'dev2@nemo'", uid(2, 0x81)) == 1
    was = ok(conn, "lab_import_job", {"job_id": job, "provider_org_id": NEMO})
    assert (was.get("state"), was.get("error")) == ("failed", "the source is gone"), was
    assert ok(conn, "lab_import_requeue", requeue(job, uid(3, 0x81))) == again, \
        "a replayed requeue opened a second job"
    assert t.count(conn, "select count(*) from infrx.lab_import_jobs") == 2
    [claimed] = ok(conn, "lab_import_job_claim", {"worker_id": "i5", "limit": 10,
                                                  "redelivery_s": 30})
    assert (claimed.get("job_id"), claimed.get("spec")) == (again["job_id"], again["spec"])
    for args in (requeue(job, uid(4, 0x81), OTHER), requeue(uid(5, 0x81), uid(6, 0x81))):
        assert refusal(conn, "lab_import_requeue", args) == "not_found", args
    return "a new queued job naming its predecessor; replay-safe; own provider only"


@v.rolled_back
def check_only_a_failed_job_is_requeued(conn) -> str:
    """A queued, running or succeeded job is refused `state_conflict` naming its state (it is
    still the import); a requeue with no actor is `invalid_request`; a new id another job
    already holds is refused, never answered with that job."""
    queued = uid(1, 0x82)
    ok(conn, "lab_import_job_enqueue", {"job_id": queued, "provider_org_id": NEMO,
                                        "spec": SPEC, "actor": "dev@nemo"})
    got = cc.attempt(conn, "select infrx.lab_import_requeue(%s)",
                     (Jsonb(requeue(queued, uid(2, 0x82))),))
    assert got is not None and "state_conflict" in got and "is queued" in got, got
    ok(conn, "lab_import_job_claim", {"worker_id": "i5", "limit": 10, "redelivery_s": 30})
    assert refusal(conn, "lab_import_requeue", requeue(queued, uid(2, 0x82))) == \
        "state_conflict"
    ok(conn, "lab_import_job_finish", {"job_id": queued, "worker_id": "i5",
                                       "state": "succeeded", "result": {"accepted": 1}})
    assert refusal(conn, "lab_import_requeue", requeue(queued, uid(2, 0x82))) == \
        "state_conflict"
    job = failed(conn, 0x83)
    assert refusal(conn, "lab_import_requeue", requeue(job, uid(2, 0x83), actor=None)) == \
        "invalid_request"
    assert refusal(conn, "lab_import_requeue", requeue(job, queued)) == "state_conflict", \
        "a taken id answered as the new job"
    assert t.count(conn, "select count(*) from infrx.lab_import_jobs") == 2
    return "queued/running/succeeded refused by state; no actor or a taken id refused"


def check_the_requeue_store_composes(conn) -> str:
    """`PgLabImportJobs.requeue` round trip (commits; tag 0x84)."""
    from infrx.state.lab_data import PgLabImportJobs
    job = failed(conn, 0x84)
    jobs = PgLabImportJobs(connector(pgharness.dsn(conn.info.dbname)))
    again = asyncio.run(jobs.requeue(job, new_job_id=uid(2, 0x84), provider_org_id=NEMO,
                                     actor="dev2@nemo"))
    assert [again.get(k) for k in ("job_id", "requeued_from", "state")] == \
        [uid(2, 0x84), job, "queued"], again
    return "the store requeues"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_variants_are_the_providers_own_with_their_latest_comparison,
    check_a_failed_import_is_requeued_as_a_new_job_naming_its_predecessor,
    check_only_a_failed_job_is_requeued,
    check_the_requeue_store_composes)}


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
def test_lw7(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
