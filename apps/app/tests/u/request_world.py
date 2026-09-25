#!/usr/bin/env python3
"""U4 on real PostgreSQL: owned request detail and result lifecycle (USER-RESULTS, RESULT-EXPIRY,
CONSOLE-TENANT). Builds requests through the real admission/claim/terminalize functions on the
task-local database (INFRX_D_TASK=app-u4 -> 127.0.0.1:55456, container infrx-app-u4-postgres),
records what the API's own read path (`PgJobStore.get_owned_credit` + F2C.b `read_outcome`, the
gateway's `GET /v1/jobs/{handle}/result` classification) answers for each request at three
store-clock instants, then runs `tests/u/request-pg.test.ts`, which reads the same requests
through the App's unchanged `postgrestRequestReads` as the real browser principal and compares.

    cd apps/infrx-api && INFRX_D_TASK=app-u4 uv run --frozen python ../app/tests/u/request_world.py

Reuses the D harness (tests/d) - no admission, hold, settlement or result SQL is written here. The
container is removed when this process exits, so the Node suite runs inside it. Exit code = the
Node suite's.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

API = Path.cwd()
sys.path.insert(0, str(API))

from infrx.contracts.conformance import builders as b  # noqa: E402
from infrx.contracts.v2.lifecycle import read_outcome  # noqa: E402
from infrx.state import migrations  # noqa: E402
from infrx.state.jobstore import PgJobStore, connector  # noqa: E402

from tests.d import checks_admission as ca  # noqa: E402
from tests.d import checks_credit as cc  # noqa: E402
from tests.d import checks_leases as cl  # noqa: E402
from tests.d import checks_settle as cs  # noqa: E402
from tests.d import pgharness  # noqa: E402

APP = Path(__file__).resolve().parents[2]
DB = f"{pgharness.DATABASE}_request"
ME, OTHER = cc.CONSUMER_1, cc.CONSUMER_2
KEPT_TTL = float(cs.LIMITS["result_ttl_s"])     # the configuration the first results settled under
SHORT_TTL = 60.0                                  # the configuration changed before `short` settled
INSTANTS = (0.0, 120.0, KEPT_TTL + 1.0)           # seconds after the world was built


def conn():
    c = pgharness.connect(DB)
    c.execute("set statement_timeout = '30s'")
    return c


def running(c, user: str, key: str, worker: str):
    """A leased CREDIT job of `user` (checks_leases.credit_running, for any individual)."""
    world = ca.World(c)
    request = cl.gateway_request(world, org_id=cc.personal_org(c, user), key_id=key,
                                 model_revision=ca.PIN)
    ca.admit(c, request, b.idem(request, request.request_id), regime="credit")
    _, prep = cl.claim(c, request.request_id)
    assert cl.prepare(c, prep["lease"])[0] is None, "the fixture did not queue"
    code, answer = cl.d3(c, "claim", job_id=request.request_id, worker_id=worker)
    assert code is None, f"not claimable: {code}"
    return request.request_id, cl.lease_of(answer)


def settle(c, lease, proposal: dict, ttl: float = KEPT_TTL) -> None:
    """`terminalize` as the worker calls it, under the result TTL configured at that moment."""
    code, _ = cs.outcome(c, "terminalize", {"lease": lease.model_dump(mode="json"),
                                            "outcome": proposal, "regime": "credit",
                                            "limits": {**cs.LIMITS, "result_ttl_s": ttl}})
    assert code is None, code


def build() -> dict[str, str]:
    jobs: dict[str, str] = {}
    with conn() as c:
        ca.seed_admission(c)
        job, lease = running(c, ME, ca.C1_KEY, "w-kept")
        settle(c, lease, cs.propose(job, usage=(1200, 340), ref=cs.stored(c, job)))
        jobs["kept"] = job
        # The operator shortens the result TTL; results already settled keep their promise.
        job, lease = running(c, ME, ca.C1_KEY, "w-short")
        settle(c, lease, cs.propose(job, usage=(800, 120), ref=cs.stored(c, job)), ttl=SHORT_TTL)
        jobs["short"] = job
        job, lease = running(c, ME, ca.C1_KEY, "w-unknown")
        cl.publish(c, lease)
        settle(c, lease, cs.propose(job, ref=cs.stored(c, job)))          # usage never reported
        jobs["unknown"] = job
        job, lease = running(c, ME, ca.C1_KEY, "w-failed")
        settle(c, lease, cs.propose(job, "engine_error", "failed"))
        jobs["failed"] = job
        jobs["running"], _ = running(c, ME, ca.C1_KEY, "w-running")
        queued = ca.credit_request(ca.World(c), ca.C1_KEY, cc.personal_org(c, ME))
        ca.admit(c, queued, b.idem(queued, queued.request_id), regime="credit")
        jobs["queued"] = queued.request_id
        job, lease = running(c, OTHER, ca.C2_KEY, "w-theirs")
        settle(c, lease, cs.propose(job, usage=(1000, 100), ref=cs.stored(c, job)))
        jobs["theirs"] = job
        cs.assert_no_drift(c, "the U4 world")
    return jobs


async def api_reads(jobs: dict[str, str]) -> dict[str, str]:
    """What the gateway's owned result read classifies each job as, on the store clock."""
    store = PgJobStore(connector(pgharness.dsn(DB)))
    now = await store.db_now()
    answers = {}
    with conn() as c:
        owners = dict(c.execute("select request_id::text, org_id::text || '|' || job_handle "
                                "from infrx.jobs where request_id = any(%s::uuid[])",
                                (list(jobs.values()),)).fetchall())
    for name, job in jobs.items():
        org, handle = owners[job].split("|")
        _, outcome = await store.get_owned_credit(org, handle)
        answers[name] = str(read_outcome(outcome, now))
    return answers


def main() -> int:
    reason = pgharness.unavailable()
    if reason is not None:
        print(f"SKIP: task-local PostgreSQL unavailable: {reason}")
        return 0
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    jobs = build()
    with conn() as c:
        base = c.execute("select extract(epoch from offset_s)::float8 from infrx_test.clock").fetchone()[0]
    api = {}
    for delta in INSTANTS:
        with conn() as c:
            c.execute("select infrx_test.set_offset(%s)", (base + delta,))
        api[str(int(delta))] = asyncio.run(api_reads(jobs))
    with conn() as c:
        c.execute("select infrx_test.set_offset(%s)", (base,))
    world = {"user": ME, "other": OTHER, "jobs": jobs, "api": api, "base_offset": base,
             "instants": [int(d) for d in INSTANTS], "kept_ttl": KEPT_TTL, "short_ttl": SHORT_TTL,
             "provider": cc.PROVIDER_ADMIN_USER, "ungranted": cc.UNGRANTED}
    print(f"U4 world: {len(jobs)} requests; API reads {json.dumps(api)}")
    run = subprocess.run(["node", "--test", "tests/u/request-pg.test.ts"], cwd=APP,
                         env={**os.environ, "U4_PG_DSN": pgharness.dsn(DB),
                              "U4_PG_WORLD": json.dumps(world)})
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
