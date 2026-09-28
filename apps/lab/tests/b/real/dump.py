"""B4's real half: D7 run records and B2 reports produced by the merged Python, not by hand.

Runs B1 (`freeze` + `Runner.run`) on the task-local PostgreSQL (0001-0029, D7's world, the b3
key) through B1's own PG drill world, reads each run back through `lab_run_status` (the record
the Lab's `Run` type is), and compares case records through B2's `compare` (the Lab's `Report`
type). Writes runs.json and reports.json beside this file; tests/b/real.test.ts renders them.

    cd apps/infrx-api && INFRX_D_TASK=b3 uv run --frozen python ../lab/tests/b/real/dump.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path.cwd()))
from infrx.state import migrations  # noqa: E402
from infrx.state.jobstore import connector  # noqa: E402
from infrx.state.lab_data import PgLabDataStore  # noqa: E402
from tests.b.reports import test_reports as b2  # noqa: E402
from tests.b.runner import test_runner_pg as pg  # noqa: E402
from tests.b.runner.world import NEMO, serve  # noqa: E402
from tests.d import pgharness  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent


def main() -> None:
    pgharness.ensure()
    pgharness.recreate(pg.DB)
    pgharness.apply(pg.DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(pg.DB) as conn:
        pg.d7.seed(conn)
        world = (conn, PgLabDataStore(connector(pgharness.dsn(pg.DB))))
        done, cancelled, queued = pg.Case(world, 11), pg.Case(world, 12), pg.Case(world, 13)
        server = serve(done.wallet, pg.PORT, pg.KEY)
        try:
            pg.run(done.runner().run(done.frozen))
        finally:
            server.shutdown()
            server.server_close()

        def answer(prompt):                     # B1's durable-cancel drill: cancel at case 2
            if prompt.endswith("q2"):
                with psycopg.connect(pgharness.dsn(pg.DB), autocommit=True) as other:
                    pg.l2.call(other, "lab_cancel_run", {"provider_org_id": NEMO,
                                                         "run_id": cancelled.run_id})
            return "a" + prompt.split("q")[-1]
        cancelled.wallet.answer = answer
        server = serve(cancelled.wallet, pg.PORT, pg.KEY)
        try:
            pg.run(pg.Runner(cancelled.store, cancelled.objects, cancelled.endpoint(),
                             pg.DEPLOYMENT, worker_id="w", limits=pg.Limits(30, 3, 2, 1))
                   .run(cancelled.frozen))
        finally:
            server.shutdown()
            server.server_close()
        store = world[1]
        runs = {name: pg.run(store.run_status(c.run_id, provider_org_id=NEMO))
                for name, c in (("succeeded", done), ("cancelled", cancelled),
                                ("queued", queued))}
    # B2 over case records: a hidden safety regression under an aggregate win (reject), and
    # a tie with a missing case, an error, an unscored case and PROVIDER_USD judge costs
    # beside CREDIT (inconclusive: coverage).
    base = b2.cases([1.0] * 5 + [0.0] * 30 + [1.0] * 5)         # B2's own hidden-regression
    cand = b2.cases([1.0, 0.0, 0.0, 0.0, 0.0] + [1.0] * 35)
    gaps = b2.cases([1.0] * 40)[:-1]
    gaps[5] = {**gaps[5], "error": "timeout"}
    gaps[6] = {**gaps[6], "score": None}
    gaps[7] = {**gaps[7], "costs": [{"unit": "CREDIT", "value": "1.00000000"},
                                    {"unit": "PROVIDER_USD", "value": "0.25000000"}]}
    reports = {"reject": b2.compare(base, cand),
               "inconclusive": b2.compare(b2.cases([1.0] * 40), gaps)}
    for name, data in (("runs", runs), ("reports", reports)):
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print({k: v["state"] for k, v in runs.items()},
          {k: v["decision"] for k, v in reports.items()})


if __name__ == "__main__":
    main()
