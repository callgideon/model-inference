#!/usr/bin/env python3
"""C7-RV-6 on real PostgreSQL (R248, proposed ruling): `/lab/v1/releases`' records as
`LAB_RELEASES` composes them (`pilot.lab_releases`: D9's listing 0048, 0054's Live, D7's
policy, 0055's variants) when one release's jobs settled in legacy USD. 0054's Live refuses
that release (`InvalidRequest`, never converted); the listing still answers: that row alone
shows `progress`/`verdict` null with `refused: "unit_refused"`, the other release its Live.
Only the objects are a stand-in (in memory; the unit's are the Lab bucket).

    INFRX_D_TASK=p3 uv run --frozen pytest -q tests/g/lab_releases/test_lab_releases_unit_refused_pg.py

Failure oracle: one legacy-USD release failing the whole page (the other releases unreadable),
or a refused row shown with an invented progress or a verdict.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.gateway import pilot
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.state.jobstore import connector

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d9_rollout as d9
from ...d.test_code_mutants_live import job
from ...r.control.test_control import plan

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "p3" else \
    "PostgreSQL only on the p3 task-local key (INFRX_D_TASK=p3)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_c7g"
OPERATOR = "00000092-0000-4000-8000-000000000092"


@pytest.fixture(scope="module")
def world():
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d9.seed(conn)
        yield conn


def test_lab_releases_unit_refused_pg__a_legacy_usd_release_nulls_only_its_own_row(
        world, monkeypatch, tmp_path):
    dsn = pgharness.dsn(DB)
    objects = InMemoryObjectStore()
    monkeypatch.setattr(lab_workers, "lab_objects", lambda mode, env: objects)
    stored = tmp_path / "plan.json"
    stored.write_text(plan().model_dump_json())
    launched = []
    for tag in (41, 42):
        payload = d9.policy(d9.uid(tag, 0xc7), weights=(1_000,), endpoint=d9.uid(tag, 0xe7))
        ref = d7.publish(world, payload)
        assert lab_workers.main(["rollout", "launch", "--policy-ref", ref, "--plan", str(stored),
                                 "--reason", "canary"], env={
            "LAB_DATABASE_URL": dsn, "LAB_S3_BUCKET": "lab", "LAB_OPERATOR_ID": OPERATOR}) == 0
        launched.append((ref, payload))
    (usd, usd_payload), (credit, credit_payload) = launched
    job(world, usd, usd_payload, d9.uid(1, 0x41), usd_payload["candidates"][0]["serving_ref"],
        "succeeded", charged="0.25000000", regime="legacy_usd")
    job(world, credit, credit_payload, d9.uid(2, 0x42),
        credit_payload["candidates"][0]["serving_ref"], "failed", ms=40)
    records = pilot.lab_releases(connector(dsn), None, None, objects).records
    shown = {r["policy_ref"]: r for r in asyncio.run(records.releases(d9.NEMO))}
    refused = shown[usd]
    assert (refused["progress"], refused["verdict"], refused.get("refused")) == \
        (None, None, "unit_refused"), refused
    listed = shown[credit]
    assert listed["progress"]["candidate"] == {"requests": 1, "errors": 1, "p99_ms": 40}, listed
    assert listed["progress"]["spent"]["unit"] == "CREDIT" and "refused" not in listed
