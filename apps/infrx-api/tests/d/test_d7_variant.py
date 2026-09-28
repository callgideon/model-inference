#!/usr/bin/env python3
"""D7 / WR-R3-2 (wave-5 LW2, lab-sql; the rollout-control lane's R3 request): R3's
`infrx.variant_comparison.1` stored beside its B2 report on real PostgreSQL - EVAL-REPRO and
PIPELINE-LINEAGE for `0040_lab_variant_comparisons.sql`, composed with `PgLabDataStore`.

World: test_d7_followup's (NEMO's run records, B2 reports). Each `check_*` is the check a
mutant in `code_mutants_d7.py` (`VARIANT`) must break.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d7_variant.py
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest
from infrx.contracts.lab import records
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from . import checks_credit as cc
from . import pgharness
from . import test_d7_followup as f

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d7v"

t, NEMO, OTHER, uid = f.t, f.NEMO, f.OTHER, f.uid
ok, refusal, rolled_back = f.ok, f.refusal, f.rolled_back
seed = f.seed


def variant(conn, tag: int, provider: str = NEMO) -> str:
    serving = f"lab:serving:{provider}:{{}}@sha256:{'e' * 64}"
    return t.publish(conn, {"schema": "lab.optimization_variant.1", "provider_org_id": provider,
                            "variant_id": uid(1, tag), "base_serving_ref": serving.format(
                                uid(2, tag)), "variant_serving_ref": serving.format(uid(3, tag)),
                            "changes": ["fp8 weights"]}, provider)


def comparison(variant_ref: str, report_digest: str, outcome: str = "equivalent",
               claimed: bool = True) -> dict:
    return {"schema": "infrx.variant_comparison.1", "variant_ref": variant_ref,
            "outcome": outcome, "reasons": [], "capabilities": {"base": ["chat"],
                                                                "variant": ["chat"]},
            "report_digest": report_digest, "performance": None,
            "optimization_claimed": claimed}


def put(doc: dict, provider: str = NEMO) -> dict:
    return {"provider_org_id": provider, "actor": "r3",
            "body": records.canonical(doc).decode()}


def stored_report(conn, tag: int) -> str:
    rep = f.report(conn, tag)
    return ok(conn, "lab_put_eval_report", f.put_report(conn, rep))["report_digest"]


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads comparisons or executes their RPCs; the platform
    role reads them and writes only through the RPC; a stored comparison is never edited."""
    probes = ["select count(*) from infrx.lab_variant_comparisons",
              "select infrx.lab_put_variant_comparison('{}'::jsonb)",
              "select infrx.lab_variant_comparisons('{}'::jsonb)"]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached comparisons: {reached}"
    assert cc.refused_as(conn, "service", probes[0]) is None
    got = cc.refused_as(conn, "service", "delete from infrx.lab_variant_comparisons")
    assert got is not None and got.startswith("42501"), got
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused"


@rolled_back
def check_a_comparison_is_stored_once_beside_its_report(conn) -> str:
    """WR-R3-2 / EVAL-REPRO: a comparison is stored as its RFC 8785 bytes under their sha256;
    again is the same digest and no second row; it reads back under its report for its
    provider only; it is never edited, and the digest is its content's."""
    report, var = stored_report(conn, 0xc1), variant(conn, 0xc1)
    doc = comparison(var, report)
    digest = ok(conn, "lab_put_variant_comparison", put(doc))["comparison_digest"]
    assert digest == "sha256:" + hashlib.sha256(records.canonical(doc)).hexdigest()
    assert ok(conn, "lab_put_variant_comparison", put(doc))["comparison_digest"] == digest
    assert t.count(conn, "select count(*) from infrx.lab_variant_comparisons") == 1
    rows = ok(conn, "lab_variant_comparisons", {"provider_org_id": NEMO,
                                                "report_digest": report})
    assert [r["comparison_digest"] for r in rows] == [digest], rows
    assert ok(conn, "lab_variant_comparisons", {"provider_org_id": OTHER,
                                                "report_digest": report}) == []
    got = cc.attempt(conn, "update infrx.lab_variant_comparisons set body = body")
    assert got is not None and got.startswith("23514"), got
    forged = cc.attempt(conn, "insert into infrx.lab_variant_comparisons select %s, "
                        "provider_org_id, variant_ref, report_digest, outcome, "
                        "optimization_claimed, body, stored_by from "
                        "infrx.lab_variant_comparisons", (f"sha256:{'0' * 64}",))
    assert forged is not None and "content_addressed" in forged, forged
    return "write-once by content digest, under its report, provider-bound"


@rolled_back
def check_a_comparison_rests_on_the_providers_variant_and_report(conn) -> str:
    """PIPELINE-LINEAGE: the variant is a published `lab:variant` record of the provider and
    the report is the provider's B2 report (else `not_found`); an optimization is claimed
    only on an `equivalent` outcome, and only this schema is stored (`invalid_request`)."""
    report, var = stored_report(conn, 0xc2), variant(conn, 0xc2)
    foreign_var = variant(conn, 0xc3, OTHER)
    run = t.publish(conn, t.eval_run(uid(9, 0xc2), t.publish(conn, t.manifest(
        uid(8, 0xc2), n=1, tag=0xc2)), t.publish(conn, t.harness(uid(7, 0xc2)))))
    got = {"foreign_variant": refusal(conn, "lab_put_variant_comparison",
                                      put(comparison(foreign_var, report))),
           "not_a_variant": refusal(conn, "lab_put_variant_comparison",
                                    put(comparison(run, report))),
           "unknown_report": refusal(conn, "lab_put_variant_comparison",
                                     put(comparison(var, f"sha256:{'9' * 64}"))),
           "foreign_report": refusal(conn, "lab_put_variant_comparison",
                                     put(comparison(var, report), OTHER))}
    assert got == dict.fromkeys(got, "not_found"), got
    bad = {"claim_unproven": comparison(var, report, "inconclusive", True),
           "unknown_outcome": comparison(var, report, "better", False),
           "schema": {**comparison(var, report), "schema": "x"}}
    got = {k: refusal(conn, "lab_put_variant_comparison", put(v)) for k, v in bad.items()}
    assert got == dict.fromkeys(bad, "invalid_request"), got
    assert ok(conn, "lab_put_variant_comparison", put(comparison(
        var, report, "not_equivalent", False)))["comparison_digest"].startswith("sha256:")
    return "own variant and report only; claims only when equivalent"


def check_the_store_composes(conn) -> str:
    """`PgLabDataStore.put_variant_comparison` / `variant_comparisons` round trip."""
    with conn.transaction():
        report, var = stored_report(conn, 0xc4), variant(conn, 0xc4)
    store = PgLabDataStore(connector(pgharness.dsn(conn.info.dbname)))
    doc = comparison(var, report)

    async def go():
        digest = await store.put_variant_comparison(doc, provider_org_id=NEMO, actor="r3")
        return digest, await store.variant_comparisons(report, provider_org_id=NEMO)
    digest, back = asyncio.run(go())
    assert digest == "sha256:" + hashlib.sha256(records.canonical(doc)).hexdigest()
    assert back == [doc], back
    return "stored and read back byte-identical"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_a_comparison_is_stored_once_beside_its_report,
    check_a_comparison_rests_on_the_providers_variant_and_report, check_the_store_composes)}


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
def test_d7_variant(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
