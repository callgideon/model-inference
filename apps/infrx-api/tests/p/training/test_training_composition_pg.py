#!/usr/bin/env python3
"""WR-P3-D8-C / WR-P1-D8-C on the real stores (p3's key; P1's proof rides on the same
database): the gateway's `LAB_PIPELINES` composition (`pilot._lab`) over one connector -
D7's `PgLabDataStore`, D8's `PgRunLedger` (0042: the run's CAS and ONE PROVIDER_USD
reservation per submit key on the named payer's D6J budget, `lab_submission` on) and D8's
`PgLabelLog`, L2's `PgAccessStore` - driven through the route's own handlers
(`infrx.gateway.routes.lab_pipelines`) as the Lab's developer. The world is
`test_training_services.py`'s (an N2 version and its export). The run listings are not
written yet (WR-LAB2-4): a typed 503.

T2I/G8's pattern: outside the mutant runner; the fake-level oracle is
`tests/g/test_startup.py::test_lab_api_2__the_pipeline_surface_is_p1_and_p3_on_d8s_ledgers`
and its `lab_pipelines_*` mutants.

    INFRX_D_TASK=p3 uv run --frozen pytest -q tests/p/training/test_training_composition_pg.py
"""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest
from infrx.contracts import errors
from infrx.gateway import pilot
from infrx.gateway.routes import lab_pipelines as routes
from infrx.state.jobstore import connector

from tests.g import support

from ...d import pgharness
from ...d import test_l2sql_access as l2
from ...n.imports.world import NEMO, run
from ...n.versions.test_versions import uid
from ..annotations.test_annotations import label_rows
from ..annotations.world import RUBRIC
from .test_training_services import DB, d8_holds, d8_run, pytestmark, world  # noqa: F401
from .world import PAYER


def composed(objects):
    settings = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                               lab_pipelines=True))
    return pilot._lab(settings, connect=connector(pgharness.dsn(DB)),
                      objects=objects)["lab_pipelines"]


def test_p3_pg_the_composed_pipeline_surface_reserves_once_on_the_named_payer(world) -> None:
    conn, store, objects, ref, export = world
    x, who = composed(objects), SimpleNamespace(provider_org_id=NEMO, user_id=l2.DEV)
    ext = uid(9, 0xe1)
    body = routes.PrepareBody(
        external_run_id=ext, dataset_ref=ref,
        export={"format": export["format"], "export_id": export["export_id"]},
        config={"objective": "sft", "adaptation": "lora", "base_model": "marlin-2b"},
        payer_ref=PAYER, limit="25.00000000")
    prepared = run(routes.prepare(x, who, body))
    assert (prepared["state"], prepared["payer_ref"], d8_run(conn, ext)) == (
        "prepared", PAYER, "prepared")
    submitted = run(routes.move(x, who, ext, "submit"))
    assert submitted["state"] == d8_run(conn, ext) == "submitted"
    assert d8_holds(conn, ext) in ([], [("held", 1)])       # manual: nothing platform-paid
    assert run(routes.move(x, who, ext, "submit"))["state"] == "submitted"   # replay: once
    with pytest.raises(errors.DependencyUnavailable):
        run(routes.training_runs(x, who))


def test_p1_pg_the_composed_pipeline_surface_logs_labels_in_d8(world) -> None:
    conn, store, objects, ref, export = world
    x, who = composed(objects), SimpleNamespace(provider_org_id=NEMO, user_id=l2.DEV)
    before = conn.execute("select count(*) from infrx.lab_label_events").fetchone()[0]
    ids = sorted(s.sample_id for s in run(store.resolve(ref, provider_org_id=NEMO)).samples)
    body = routes.ImportBody(import_id=uid(9, 0x1c), dataset_ref=ref, rubric_ref=RUBRIC,
                             rows="\n".join(json.dumps(r) for r in label_rows(ids)))
    receipt = run(routes.import_labels(x, who, body))
    assert receipt["accepted"] == len(ids) and receipt["rejected"] == []
    assert run(routes.import_labels(x, who, body)) == receipt           # one receipt per id
    after = conn.execute("select count(*) from infrx.lab_label_events").fetchone()[0]
    assert after - before == len(ids)
    assert len(run(routes.labels(x, who, ref))) == len(ids)
