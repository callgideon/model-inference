#!/usr/bin/env python3
"""C0 (CONSOLE-TENANT): run the App's consumer read port against a REAL task-local stack.

The pinned Supabase PostgreSQL image with every migration applied (the D harness, task
`app-c0`: container `infrx-app-c0-postgres-supabase` on 127.0.0.1:55451), the pinned
PostgREST v13.0.4 in this lane's own container and network, signed JWTs, no service key.
The App test (`tests/c/consumer-postgrest.test.ts`) drives `postgrestPort` and
`createConsumerReads` through supabase-js exactly as `lib/services/server.ts` composes them.

    cd apps/infrx-api && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase \\
        uv run --frozen python ../app/tests/c/realdb/stack.py

Everything is created here, labelled with this checkout, and removed at exit (the
PostgreSQL container by the D harness's own atexit hook). Nothing hosted is touched.
The JWT secret and database password are local, per-container test values.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("INFRX_D_TASK", "app-c0")
os.environ.setdefault("INFRX_D1_IMAGE", "supabase")
APP = Path(__file__).resolve().parents[3]
API = APP.parent / "infrx-api"
sys.path.insert(0, str(API))

import httpx  # noqa: E402
from infrx.contracts.conformance import builders as b  # noqa: E402
from infrx.state import migrations  # noqa: E402
from tests.d import checks_admission as ca  # noqa: E402
from tests.d import checks_content as ck  # noqa: E402
from tests.d import checks_credit as cc  # noqa: E402
from tests.d import checks_leases as cl  # noqa: E402
from tests.d import pgharness  # noqa: E402

assert pgharness.SERVICE.host_port == 55451 and pgharness.ON_SUPABASE, \
    "run with INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase (this lane's port, the real image)"

POSTGREST = ("postgrest/postgrest@sha256:"
             "a312f4b2e48530a01fc26f5310d547d6c26d087858360e164522e415723a7732")  # v13.0.4
NAME, NETWORK, DB_ALIAS = "infrx-app-c0-postgrest", "infrx-app-c0-net", "infrx-app-c0-db"
LABEL = "ai.infrx.app-c0.checkout"
JWT_SECRET = "infrx-app-c0-local-jwt-secret-not-a-real-one"
AUTHN_PASSWORD = "infrx-app-c0-authenticator-local"
DB = f"{pgharness.DATABASE}_rest"

OPERATOR = "c0000000-0000-4000-8000-00000000000a"
EMPTY = "c0000000-0000-4000-8000-00000000000b"      # granted, no requests
LARGE = "c0000000-0000-4000-8000-00000000000c"      # granted, 20,000 extra ledger entries
FILLER = "c0000000-0000-4000-8000-00000000000d"     # granted, 60,000 entries elsewhere
REVOKED_KEY = "c7000000-0000-4000-8000-0000000000c1"
SHARED_KEY = "c7000000-0000-4000-8000-0000000000c2"
LARGE_ROWS, FILLER_ROWS = 20_000, 60_000


def _docker(*args, check=True):
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


def _ours(name: str, kind: str) -> bool | None:
    probe = _docker(kind, "inspect", "-f", "{{json .Config.Labels}}" if kind == "container"
                    else "{{json .Labels}}", name, check=False)
    if probe.returncode != 0:
        return None
    return (json.loads(probe.stdout.strip() or "null") or {}).get(LABEL) == pgharness.checkout()


def up() -> str:
    for name, kind in ((NAME, "container"), (NETWORK, "network")):
        if _ours(name, kind) is False:
            raise SystemExit(f"refusing to touch {kind} {name}: not created by this checkout")
    _docker("rm", "-f", NAME, check=False)
    if _ours(NETWORK, "network") is None:
        _docker("network", "create", "--label", f"{LABEL}={pgharness.checkout()}", NETWORK)
    _docker("network", "connect", "--alias", DB_ALIAS, NETWORK, pgharness.CONTAINER, check=False)
    pgharness._sb("postgres", f"alter role authenticator with login password '{AUTHN_PASSWORD}'")
    # As in tests/d/test_postgrest_d10.py: the hosted `auth.uid()` reads `request.jwt.claims`.
    pgharness._sb(DB, "create or replace function auth.uid() returns uuid language sql stable "
                      "as $f$ select coalesce(nullif(current_setting('request.jwt.claim.sub', "
                      "true), ''), nullif(nullif(current_setting('request.jwt.claims', true), "
                      "'')::jsonb ->> 'sub', ''))::uuid $f$")
    # PGRST_DB_PLAN_ENABLED: the test asks PostgREST for the plan of the adapter's own request
    # (the bounded-query oracle); it is a test-stack setting, not a hosted one.
    _docker("run", "-d", "--name", NAME, "--network", NETWORK,
            "--label", f"{LABEL}={pgharness.checkout()}",
            "-e", f"PGRST_DB_URI=postgres://authenticator:{AUTHN_PASSWORD}@{DB_ALIAS}:5432/{DB}",
            "-e", "PGRST_DB_SCHEMAS=public", "-e", "PGRST_DB_ANON_ROLE=anon",
            "-e", "PGRST_DB_PLAN_ENABLED=true", "-e", f"PGRST_JWT_SECRET={JWT_SECRET}", POSTGREST)
    address = _docker("inspect", "-f", "{{(index .NetworkSettings.Networks \"" + NETWORK
                      + "\").IPAddress}}", NAME).stdout.strip()
    base = f"http://{address}:3000"
    for _ in range(60):
        try:
            if httpx.get(base + "/", timeout=2).status_code < 500:
                return base
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"{NAME} never answered")


def down() -> None:
    if _ours(NAME, "container"):
        _docker("rm", "-f", NAME, check=False)
    _docker("network", "disconnect", NETWORK, pgharness.CONTAINER, check=False)
    if _ours(NETWORK, "network"):
        _docker("network", "rm", NETWORK, check=False)


def _user(conn, user: str, *, operator: bool = False) -> None:
    conn.execute("insert into auth.users (id, email) values (%s, %s)", (user, f"{user[-4:]}@example.com"))
    if operator:
        conn.execute("update public.profiles set is_operator = true where id = %s", (user,))


def _bulk(conn, user: str, rows: int) -> None:
    """`rows` operator adjustments of 1e-8 CREDIT over 500 distinct instants (ties on each).

    Written as `supabase_admin` with triggers off (`session_replication_role = replica`), and
    the wallet's total moved once by the same amount: row-by-row the ledger trigger rewrites the
    one wallet row per entry, which made seeding quadratic (200 s). The invariant the test
    checks - the entries sum to the stored total - holds exactly either way.
    """
    wallet = cc.wallet_of(conn, user)
    pgharness._sb(DB, f"""set session_replication_role = replica;
        insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,
                                         actor, reason, created_at)
        select '{wallet}', 'consumer', 'operator_adjustment', 0.00000001, gen_random_uuid(),
               'c0-bulk', 'c0 bulk fixture', infrx.now() - (g % 500) * interval '1 second'
          from generate_series(1, {rows}) g;
        update infrx.credit_wallets set ledger_total = ledger_total + {rows} * 0.00000001,
               revision = revision + {rows} where wallet_id = '{wallet}';""")


def seed(conn) -> dict:
    ca.seed_admission(conn)
    world = ca.World(conn)
    c1_org = cc.personal_org(conn, cc.CONSUMER_1)
    c2_org = cc.personal_org(conn, cc.CONSUMER_2)
    shared_org = cc.personal_org(conn, cc.SHARED)
    # Two settled results: one lives 60 s, one a day; the clock moves 120 s at the end.
    short, _ = ck._settled_with_result(conn, world, result_ttl_s=60.0)
    long_, _ = ck._settled_with_result(conn, world, result_ttl_s=86_400.0)
    held = []
    for _ in range(4):           # admitted, holding CREDIT, at the SAME frozen instant
        request = cl.gateway_request(world, org_id=c1_org, key_id=ca.C1_KEY, model_revision=ca.PIN)
        ca.admit(conn, request, b.idem(request, request.request_id), regime="credit")
        held.append(str(request.request_id))
    other = cl.gateway_request(world, org_id=c2_org, key_id=ca.C2_KEY, model_revision=ca.PIN)
    ca.admit(conn, other, b.idem(other, other.request_id), regime="credit")
    # Mixed history: legacy USD in C1's personal organization, never mixed into CREDIT.
    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) values "
                 "(%s, 12.34567891, 'grant', 'c0 legacy fixture')", (c1_org,))
    conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash, "
                 "audience, revoked_at) values (%s, %s, %s, 'old', 'sk-infrx-revoked', %s, "
                 "'consumer', infrx.now())", (REVOKED_KEY, c1_org, cc.CONSUMER_1, f"hash-{REVOKED_KEY}"))
    # A key in SHARED's personal org, which CONSUMER_2 is also a member of (RLS lets C2 read it).
    conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash, "
                 "audience) values (%s, %s, %s, 'shared', 'sk-infrx-shared0', %s, 'consumer')",
                 (SHARED_KEY, shared_org, cc.SHARED, f"hash-{SHARED_KEY}"))
    conn.execute("select infrx.set_suspension(%s, true, 'other', 'c0-test', 'fixture', %s)",
                 (c2_org, f"c0-suspend-{uuid.uuid4()}"))
    _user(conn, OPERATOR, operator=True)
    for user in (EMPTY, LARGE, FILLER):
        _user(conn, user)
        cc.grant(conn, user)
    _bulk(conn, LARGE, LARGE_ROWS)
    _bulk(conn, FILLER, FILLER_ROWS)
    conn.execute("analyze infrx.credit_ledger")
    now = conn.execute("select infrx.now()").fetchone()[0]
    ck.at(conn, now + timedelta(seconds=120))

    def wallet(user):
        row = conn.execute("select wallet_id::text, ledger_total::text, reserved_total::text, "
                           "available::text from infrx.credit_wallets where owner_user_id = %s "
                           "and kind = 'consumer'", (user,)).fetchone()
        return None if row is None else dict(zip(("wallet_id", "ledger_total", "reserved_total", "available"), row))

    return {
        "users": {"c1": cc.CONSUMER_1, "c2": cc.CONSUMER_2, "ungranted": cc.UNGRANTED,
                  "shared": cc.SHARED, "provider": cc.PROVIDER_DEV_USER, "operator": OPERATOR,
                  "empty": EMPTY, "large": LARGE},
        "orgs": {"c1": c1_org, "c2": c2_org, "shared": shared_org},
        "wallets": {name: wallet(user) for name, user in
                    (("c1", cc.CONSUMER_1), ("c2", cc.CONSUMER_2), ("empty", EMPTY), ("large", LARGE))},
        "requests": {"short": str(short.request_id), "long": str(long_.request_id), "held": held,
                     "c2": str(other.request_id)},
        "keys": {"c1": ca.C1_KEY, "c1_revoked": REVOKED_KEY, "c2": ca.C2_KEY,
                 "c2_operator": ca.OPERATOR_KEY, "shared": SHARED_KEY, "stray": ca.STRAY_KEY},
        "large_rows": LARGE_ROWS + 1,
    }


def main() -> int:
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        world = seed(conn)
    base = up()
    try:
        world.update({"url": base, "jwt_secret": JWT_SECRET})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(world, handle)
        run = subprocess.run(("node", "--test", "tests/c/consumer-postgrest.test.ts"), cwd=APP,
                             env={**os.environ, "INFRX_C0_STACK": handle.name})
        os.unlink(handle.name)
        return run.returncode
    finally:
        down()


if __name__ == "__main__":
    sys.exit(main())
