#!/usr/bin/env python3
"""U1R on real PostgreSQL: build a CREDIT history under CONCURRENT requests on the task-local
database (INFRX_D_TASK=app-u1r -> 127.0.0.1:55457, container infrx-app-u1r-postgres), then run
`tests/u/credit-pg.test.ts`, which reads it through the App's own adapter and view models as the
real browser principal and compares every figure with durable ledger queries.

    cd apps/infrx-api && INFRX_D_TASK=app-u1r uv run --frozen python ../app/tests/u/credit_world.py

Reuses the D harness (tests/d: the labelled, locked, self-removing container; the admission world;
the real admit/claim/terminalize functions) - no grant, hold or settlement SQL is written here. The
container is removed when this process exits, so the Node suite runs inside it. Exit code = the
Node suite's.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

API = Path.cwd()
sys.path.insert(0, str(API))

import psycopg  # noqa: E402
from infrx.contracts import errors  # noqa: E402
from infrx.contracts.conformance import builders as b  # noqa: E402
from infrx.state import migrations  # noqa: E402
from infrx.state.jobstore import domain_error  # noqa: E402

from tests.d import checks_admission as ca  # noqa: E402
from tests.d import checks_credit as cc  # noqa: E402
from tests.d import checks_leases as cl  # noqa: E402
from tests.d import checks_settle as cs  # noqa: E402
from tests.d import pgharness  # noqa: E402

APP = Path(__file__).resolve().parents[2]
DB = f"{pgharness.DATABASE}_credit"
ME, OTHER = cc.CONSUMER_1, cc.CONSUMER_2


def conn():
    c = pgharness.connect(DB)
    c.execute("set statement_timeout = '30s'")
    return c


def flow(kind: str, failures: list, seen: list) -> None:
    """One CREDIT request of CONSUMER_1, on its own connection, taken to `kind`."""
    try:
        with conn() as c:
            world = ca.World(c)
            if kind == "queued":
                org = cc.personal_org(c, ME)
                request = ca.credit_request(world, ca.C1_KEY, org)
                ca.admit(c, request, b.idem(request, request.request_id), regime="credit")
                seen.append(kind)
                return
            request, lease = cl.credit_running(c, world, worker=f"w-{kind}-{threading.get_ident()}")
            job = request.request_id
            if kind == "settled":
                code, _ = cs.settle(c, lease, cs.propose(job, usage=(1200, 340), ref=cs.stored(c, job)), "credit")
            elif kind == "cancelled":
                code, _ = cs.settle(c, lease, cs.propose(job, "client_cancelled", "cancelled", usage=(900, 12)), "credit")
            elif kind == "unknown":
                cl.publish(c, lease)
                code, _ = cs.settle(c, lease, cs.propose(job, ref=cs.stored(c, job)), "credit")
            elif kind == "free":
                code, _ = cs.settle(c, lease, cs.propose(job, "invalid_media", "failed"), "credit")
            elif kind == "absorbed":
                code, _ = cs.settle(c, lease, cs.propose(job, "deadline_exceeded", "failed"), "credit")
            else:
                code = None                                  # "running": leave it holding
            assert code is None, f"{kind}: {code}"
            seen.append(kind)
    except Exception as failed:                              # reported, and fails the run
        failures.append(f"{kind}: {failed!r}"[:300])


def race(kinds: list[str]) -> list[str]:
    failures: list = []
    seen: list = []
    workers = [threading.Thread(target=flow, args=(k, failures, seen)) for k in kinds]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert not failures, failures
    return seen


def exhaust(n: int) -> tuple[int, int]:
    """Leave room for exactly two holds, then race `n` admissions: two admitted, the rest
    refused 402 insufficient_credit, and available never below zero."""
    with pgharness.connect(DB, autocommit=False) as c:      # a probe admission, rolled back
        world = ca.World(c)
        probe = ca.credit_request(world, ca.C1_KEY, cc.personal_org(c, ME))
        ca.admit(c, probe, b.idem(probe, probe.request_id), regime="credit")
        hold = cl.row(c, probe.request_id)["maximum_hold"]
        c.rollback()
    with conn() as c:
        wallet = cc.wallet_of(c, ME)
        total, reserved = c.execute("select ledger_total, reserved_total from infrx.credit_wallets "
                                    "where wallet_id = %s", (wallet,)).fetchone()
        target = 2 * hold + Decimal("0.00000001")
        c.execute("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id, "
                  "actor, reason) values (%s, 'consumer', 'operator_adjustment', %s, gen_random_uuid(), "
                  "'ops@test', 'U1R low-funds fixture')", (wallet, target - (total - reserved)))
    admitted, refused, other = [], [], []
    barrier = threading.Barrier(n)

    def one():
        try:
            with conn() as c:
                world = ca.World(c)
                request = ca.credit_request(world, ca.C1_KEY, cc.personal_org(c, ME))
                barrier.wait()
                ca.admit(c, request, b.idem(request, request.request_id), regime="credit")
                admitted.append(request.request_id)
        except psycopg.Error as failed:
            mapped = domain_error(failed)
            (refused if isinstance(mapped, errors.DomainError) and mapped.code == "insufficient_credit"
             else other).append(repr(mapped)[:200])
    workers = [threading.Thread(target=one) for _ in range(n)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert not other, other
    return len(admitted), len(refused)


def main() -> int:
    reason = pgharness.unavailable()
    if reason is not None:
        print(f"SKIP: task-local PostgreSQL unavailable: {reason}")
        return 0
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with conn() as c:
        ca.seed_admission(c)
        # CONSUMER_1's pilot history in USD: a grant and one legacy admission (0021's reads label it USD).
        org = cc.personal_org(c, ME)
        c.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) values (%s, 25, 'grant', 'pilot')", (org,))
        world = ca.World(c)
        usd = b.request(world, org_id=org, key_id=ca.C1_KEY)
        ca.admit(c, usd, b.idem(usd, "u1r-usd"))
        # CONSUMER_2 has one settled job of their own: it must never show up in CONSUMER_1's reads.
    race(["settled"] * 4 + ["cancelled", "unknown", "free", "absorbed", "running", "queued"])
    with conn() as c:
        other_world = ca.World(c)
        theirs = ca.credit_request(other_world, ca.C2_KEY, cc.personal_org(c, OTHER))
        ca.admit(c, theirs, b.idem(theirs, "u1r-theirs"), regime="credit")
    admitted, refused = exhaust(6)
    assert (admitted, refused) == (2, 4), (admitted, refused)
    with conn() as c:
        cs.assert_no_drift(c, "the U1R world")
    world_env = {"user": ME, "other": OTHER, "admitted": admitted, "refused": refused}
    print(f"U1R world: 10 concurrent flows + exhaustion race ({admitted} admitted, {refused} refused 402)")
    run = subprocess.run(
        ["node", "--test", "tests/u/credit-pg.test.ts"], cwd=APP,
        env={**os.environ, "U1R_PG_DSN": pgharness.dsn(DB), "U1R_PG_WORLD": json.dumps(world_env)})
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
