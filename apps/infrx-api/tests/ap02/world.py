"""AP-02's seeded world: the rows the console reads, written through the REAL paths.

The D scenario (`tests/d/checks_admission.seed_admission`: CONSUMER_1/2 granted 10,000 CREDIT,
UNGRANTED with no wallet, SHARED's personal org with CONSUMER_2 as a second member) plus, for
CONSUMER_1, the states `credit-fixture.ts` shows - settled with a result, running (held),
held_unknown, released, a legacy USD job - an operator adjustment, a second key and one job of
CONSUMER_2 (the foreign rows). Each row is made by the functions production calls (admit, claim,
put_result, terminalize, operator_adjust_credit), so the views read what they would read live.

One template per process, copied per case (`conftest.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from infrx.contracts.conformance import builders as b

from tests.d import checks_admission as ca
from tests.d import checks_credit as cc
from tests.d import checks_leases as cl
from tests.d import checks_settle as cs
from tests.d.checks import _jwt

OPERATOR = "c1000000-0000-4000-8000-0000000000f0"
SECOND_KEY = "c7000000-0000-4000-8000-0000000000f2"


@dataclass
class Seeded:
    me: str = cc.CONSUMER_1
    them: str = cc.CONSUMER_2
    nobody: str = cc.UNGRANTED
    operator: str = OPERATOR
    my_org: str = ""
    their_org: str = ""
    shared_org: str = ""
    settled: str = ""          # succeeded, settled, result readable
    running: str = ""          # running, hold held
    unknown: str = ""          # succeeded, held_unknown (published, no usage)
    failed: str = ""           # failed, released (no usage)
    legacy: str = ""           # legacy USD job
    theirs: str = ""           # CONSUMER_2's job
    my_jobs: list[str] = field(default_factory=list)


def _settle(conn, lease, cause, state, *, usage=None, ref=None):
    code, _ = cs.settle(conn, lease, cs.propose(lease.job_id, cause, state, usage=usage, ref=ref),
                        "credit")
    assert code is None, f"the fixture did not settle: {code}"


def seed(conn) -> Seeded:
    ca.seed_admission(conn)
    world = ca.World(conn)
    s = Seeded()
    s.my_org = cc.personal_org(conn, s.me)
    s.their_org = cc.personal_org(conn, s.them)
    s.shared_org = cc.personal_org(conn, cc.SHARED)
    conn.execute("insert into auth.users (id, email) values (%s, 'operator@example.com')",
                 (OPERATOR,))
    conn.execute("update public.profiles set is_operator = true where id = %s", (OPERATOR,))
    conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash) "
                 "values (%s, %s, %s, 'second', 'sk-infrx-second0', 'hash-second')",
                 (SECOND_KEY, s.my_org, s.me))

    request, lease = cl.credit_running(conn, world, worker="ap02-a")
    _settle(conn, lease, "completed", "succeeded", usage=(1200, 340),
            ref=cs.stored(conn, lease.job_id))
    s.settled = request.request_id
    cs.advance(conn, 1)

    request, lease = cl.credit_running(conn, world, worker="ap02-b")
    cl.publish(conn, lease)
    _settle(conn, lease, "completed", "succeeded", ref=cs.stored(conn, lease.job_id))
    s.unknown = request.request_id
    cs.advance(conn, 1)

    request, lease = cl.credit_running(conn, world, worker="ap02-c")
    _settle(conn, lease, "engine_error", "failed")
    s.failed = request.request_id
    cs.advance(conn, 1)

    request, _ = cl.credit_running(conn, world, worker="ap02-d")
    s.running = request.request_id
    cs.advance(conn, 1)

    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                 "values (%s, 25, 'grant', 'fixture')", (s.my_org,))
    usd = b.request(world, org_id=s.my_org, key_id=ca.C1_KEY)
    ca.admit(conn, usd, b.idem(usd, "ap02-usd"))
    s.legacy = usd.request_id
    cs.advance(conn, 1)

    theirs = ca.credit_request(world, ca.C2_KEY, s.their_org)
    ca.admit(conn, theirs, b.idem(theirs, "ap02-theirs"), regime="credit")
    s.theirs = theirs.request_id

    with conn.transaction():
        conn.execute(_jwt(OPERATOR))
        conn.execute("select public.operator_adjust_credit(%s, '-5.00000000', "
                     "'Correction of a duplicated test grant', 'ap02-adjust')", (s.me,))
    s.my_jobs = [s.settled, s.unknown, s.failed, s.running, s.legacy]
    return s
