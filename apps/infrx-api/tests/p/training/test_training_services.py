#!/usr/bin/env python3
"""P3 on the real services of its task-local key `p3`: D7's `PgLabDataStore` on PostgreSQL
57530 (the external run is a `lab.external_run.1` record, checkpoints are D7 receipts), D8's
`PgRunLedger` (0042: the run's CAS along F3's machine, one PROVIDER_USD reservation per submit
key on the payer's D6J budget - `lab_submission` on and a budget for PAYER; WR-P3-D8), and
the automatic-connector protocol test server over TCP on 57531 (a real client timeout after
the server accepted). B3's evaluations stay the fake (P3's `Evaluations` port).

T2I/G8's pattern: outside the mutant runner; the oracles are the fake-world cases' mutants.

    INFRX_D_TASK=p3 uv run --frozen pytest -q tests/p/training/test_training_services.py
"""
from __future__ import annotations

import json
import os
import threading
import time

import httpx
import pytest
import uvicorn
from infrx.contracts import errors
from infrx.contracts.tasklocal import local_services
from infrx.datasets import imports, versions
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import training as p3
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore
from infrx.state.lab_pipeline import PgRunLedger

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...n.imports.world import NEMO, chunks, fixture, run
from ...n.versions.test_versions import uid
from ..annotations.world import DEV, NOW, members, rows
from .test_training import CONFIG
from .world import PAYER, FakeEvaluations, descriptor, digest, protocol_app

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "p3" else \
    "real services only on p3's task-local key (INFRX_D_TASK=p3)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local services unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_p3"
ON = frozenset({p3.MANUAL, "protocol-test"})


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                     "values ('lab_submission', true, 'p3-test', 'the paid path reserves')")
        d7.ok(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": PAYER,
                                       "limit": "100.00000000", "actor": "ops",
                                       "reason": "p3-test"})
        store, objects = PgLabDataStore(connector(pgharness.dsn(DB))), InMemoryObjectStore()
        spec, _ = fixture("benchmark")
        spec = {**spec, "import_id": uid(1, 0x1d), "dataset_id": uid(1, 0xdd),
                "grant_ref": d7.W["grant"],
                "fields": {"content": "q", "group": "g", "split": "split"}}
        data = b"".join(json.dumps(i).encode() + b"\n"
                        for i in rows(9, splits=("train", "holdout", "validation")))
        ref = run(imports.Importer(store, objects).run(
            spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
        export = run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=ref,
                                     export_id=uid(1, 0xef), now=NOW, ttl_s=3600))
        yield conn, store, objects, ref, export


def prepare(w, ext, connector_name=p3.MANUAL, ledger=None):
    conn, store, objects, ref, export = w
    ledger = ledger or PgRunLedger(connector(pgharness.dsn(DB)))
    run(p3.prepare(store, objects, ledger, provider_org_id=NEMO, actor="dev@nemo",
                   external_run_id=ext, dataset_ref=ref, config=CONFIG, export=export,
                   payer_ref=PAYER, limit="25.00000000", now=NOW, connector=connector_name))
    return ledger


def checkpoint(w, ledger, evals, ext, n, data, declared=None):
    conn, store, objects, ref, export = w
    key = f"lab/{NEMO}/training/{ext}/checkpoints/{n}"
    objects.seed(key, data)
    return run(p3.import_checkpoint(store, objects, ledger, evals, provider_org_id=NEMO,
                                    external_run_id=ext, checkpoint_id=uid(n, 0xca),
                                    artifact_key=key, artifact_digest=declared or digest(data)))


def receipt_state(conn, n) -> str:
    return conn.execute("select state from infrx.lab_checkpoint_receipts where checkpoint_id "
                        "= %s", (uid(n, 0xca),)).fetchone()[0]


def d8_run(conn, ext) -> str | None:
    row = conn.execute("select state from infrx.lab_external_runs where external_run_id = %s",
                       (ext,)).fetchone()
    return row and row[0]


def d8_holds(conn, ext) -> list:
    """(state, count) of the run's PROVIDER_USD reservations on the payer's D6J budget."""
    return conn.execute("select state, count(*) from infrx.lab_run_reservations where key = %s "
                        "and payer_ref = %s group by state", (f"submit:{ext}", PAYER)).fetchall()


def test_p3_pg_checkpoints_are_d7_receipts_and_only_valid_ones_evaluate(world) -> None:
    """The external run is a D7 record; a valid checkpoint is a D7 receipt moved to
    `validated` and queued once (a redelivery changes nothing, other bytes under the id are
    D7's idempotency conflict); tampered bytes are a `rejected` receipt; a cancelled run's
    late checkpoint is rejected; a candidate is eligible only after the holdout run
    succeeds."""
    conn, store, objects, ref, export = world
    ext, evals = uid(1, 0xe1), FakeEvaluations()
    ledger = prepare(world, ext)
    assert d7.count(conn, "select count(*) from infrx.lab_records where kind = 'external_run' "
                    "and object_id = %s", ext) == 1
    access = members()
    run(p3.submit(store, objects, ledger, p3.ManualConnector(), access, provider_org_id=NEMO,
                  user_id=DEV, external_run_id=ext))
    good = checkpoint(world, ledger, evals, ext, 1, descriptor())
    assert good["state"] == "validated" == receipt_state(conn, 1)
    assert checkpoint(world, ledger, evals, ext, 1, descriptor()) == good and evals.calls == 1
    with pytest.raises(errors.IdempotencyConflict):
        checkpoint(world, ledger, evals, ext, 1, descriptor("other-model"))
    bad = checkpoint(world, ledger, evals, ext, 2, b"tampered", declared=digest(descriptor()))
    assert (bad["reason"], receipt_state(conn, 2)) == ("digest_mismatch", "rejected")
    with pytest.raises(errors.StateConflict):
        run(p3.approve(objects, ledger, evals, access, provider_org_id=NEMO, user_id=DEV,
                       external_run_id=ext, checkpoint_id=uid(1, 0xca)))
    evals.runs[uid(1, 0xca)]["state"] = "succeeded"
    assert run(p3.approve(objects, ledger, evals, access, provider_org_id=NEMO, user_id=DEV,
                          external_run_id=ext, checkpoint_id=uid(1, 0xca)))["approved_by"] == DEV
    run(p3.cancel(ledger, p3.ManualConnector(), provider_org_id=NEMO, external_run_id=ext))
    late = checkpoint(world, ledger, evals, ext, 3, descriptor())
    assert (late["reason"], receipt_state(conn, 3), evals.calls) == ("run_cancelled",
                                                                      "rejected", 1)
    assert d8_run(conn, ext) == "cancelled"              # WR-P3-D8: the run is D8's row


def test_p3_tcp_a_timeout_after_accept_is_one_job(world) -> None:
    """Over TCP on p3's protocol port: the server accepts and answers after the client's
    timeout; the run is `ambiguous`; the resume looks the key up and finds the one job -
    exactly one POST, one job."""
    conn, store, objects, ref, export = world
    port = local_services("p3")["protocol"].host_port
    app = protocol_app("accept_sleep", delay_s=1.0)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, f"the protocol server did not start on {port}"
        ext = uid(2, 0xe1)
        ledger = prepare(world, ext, "protocol-test")

        async def submit():
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                         timeout=0.3) as client:
                return await p3.submit(store, objects, ledger,
                                       p3.HttpConnector(client, "protocol-test"), members(),
                                       provider_org_id=NEMO, user_id=DEV, external_run_id=ext,
                                       advertised=ON)
        assert run(submit())["state"] == "ambiguous"
        time.sleep(1.2)                                  # the server's answer is long gone
        resumed = run(submit())
        assert (resumed["state"], resumed.get("job_id")) == ("submitted", "job-1")
        assert (app.state.posts, len(app.state.jobs)) == (1, 1)
        assert (d8_run(conn, ext), d8_holds(conn, ext)) == ("submitted", [("held", 1)])
    finally:
        server.should_exit = True
        thread.join(timeout=10)
