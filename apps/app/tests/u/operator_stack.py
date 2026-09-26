#!/usr/bin/env python3
"""U3 (DUR-RLS, DUR-CAP, CONSOLE-FLOWS): the operator console against a REAL task-local stack.

The pinned Supabase PostgreSQL image with every committed migration (WR-U3-1's operator RPCs
are D10's `0025_operator_console.sql`), the pinned PostgREST v13.0.4 in this lane's own
container and network, and signed JWTs for anon, authenticated individuals, the operator and
service_role. `operator-postgrest.test.ts` drives the App's own `operatorReads`,
`operatorRpcPort` and C3A's `consoleActions` through supabase-js.

    cd apps/infrx-api && INFRX_D_TASK=app-u3 INFRX_D1_IMAGE=supabase \\
        uv run --frozen python ../app/tests/u/operator_stack.py

Task `app-u3`: container `infrx-app-u3-postgres-supabase` on 127.0.0.1:55453. Same shape as C3A's
`actions_stack.py` (not imported: its module pins C3A's port). Everything is labelled with this
checkout and removed at exit; nothing hosted is touched. The JWT secret and passwords are local,
per-container test values.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("INFRX_D_TASK", "app-u3")
os.environ.setdefault("INFRX_D1_IMAGE", "supabase")
APP = Path(__file__).resolve().parents[2]
API = APP.parent / "infrx-api"
sys.path.insert(0, str(API))

import httpx  # noqa: E402
from infrx.contracts.conformance import builders as b  # noqa: E402
from infrx.state import migrations  # noqa: E402
from tests.d import checks_admission as ca  # noqa: E402
from tests.d import checks_credit as cc  # noqa: E402
from tests.d import checks_leases as cl  # noqa: E402
from tests.d import checks_settle as cs  # noqa: E402
from tests.d import pgharness  # noqa: E402

assert pgharness.SERVICE.host_port == 55453 and pgharness.ON_SUPABASE, \
    "run with INFRX_D_TASK=app-u3 INFRX_D1_IMAGE=supabase (this lane's port, the real image)"

POSTGREST = ("postgrest/postgrest@sha256:"
             "a312f4b2e48530a01fc26f5310d547d6c26d087858360e164522e415723a7732")  # v13.0.4
NAME, NETWORK, DB_ALIAS = "infrx-app-u3-postgrest", "infrx-app-u3-net", "infrx-app-u3-db"
LABEL = "ai.infrx.app-u3.checkout"
JWT_SECRET = "infrx-app-u3-local-jwt-secret-not-a-real-one"
AUTHN_PASSWORD = "infrx-app-u3-authenticator-local"
DB = f"{pgharness.DATABASE}_rest"

OPERATOR = "0b300000-0000-4000-8000-000000000003"      # a platform operator (profiles.is_operator)
DRIFT_PROVIDER = "0b300000-0000-4000-8000-0000000000d0"
DRIFT_WALLET = "0b300000-0000-4000-8000-0000000000d1"   # drifts by +1 CREDIT while the cases run


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
    _docker("run", "-d", "--name", NAME, "--network", NETWORK,
            "--label", f"{LABEL}={pgharness.checkout()}",
            "-e", f"PGRST_DB_URI=postgres://authenticator:{AUTHN_PASSWORD}@{DB_ALIAS}:5432/{DB}",
            "-e", "PGRST_DB_SCHEMAS=public", "-e", "PGRST_DB_ANON_ROLE=anon",
            "-e", f"PGRST_JWT_SECRET={JWT_SECRET}", POSTGREST)
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


def set_drift(conn, ledger_total: int) -> None:
    """The throwaway wallet's summary, written as the owner with the wallet guard off (as
    checks_settle's drift positive control does): DUR-RLS on the drift view needs drift to exist."""
    conn.execute("alter table infrx.credit_wallets disable trigger user")
    conn.execute("update infrx.credit_wallets set ledger_total = %s where wallet_id = %s",
                 (ledger_total, DRIFT_WALLET))
    conn.execute("alter table infrx.credit_wallets enable trigger user")


def seed(conn) -> dict:
    ca.seed_admission(conn)      # CONSUMER_1/2 granted, their consumer keys, C2's operator key
    conn.execute("insert into auth.users (id, email) values (%s, 'operator@example.com')",
                 (OPERATOR,))
    conn.execute("update public.profiles set is_operator = true where id = %s", (OPERATOR,))
    # One CREDIT request of CONSUMER_1 whose usage is unknown: the reconciliation queue.
    request, lease = cl.credit_running(conn, ca.World(conn), worker="u3-unknown")
    cl.publish(conn, lease)
    code, _ = cs.settle(conn, lease, cs.propose(request.request_id,
                                                ref=cs.stored(conn, request.request_id)), "credit")
    assert code is None, code
    # A provider_dev wallet 1 CREDIT past its empty ledger: the operator sees it (DB01), a
    # consumer does not (DB02). Not a consumer account, so no other case reads it.
    conn.execute("insert into infrx.provider_orgs (provider_org_id, slug, display_name, "
                 "created_by) values (%s, 'u3-drift', 'U3 drift', 'u3')", (DRIFT_PROVIDER,))
    conn.execute("insert into infrx.credit_wallets (wallet_id, kind, owner_provider_org_id) "
                 "values (%s, 'provider_dev', %s)", (DRIFT_WALLET, DRIFT_PROVIDER))
    set_drift(conn, 1)
    return {
        "users": {"c1": cc.CONSUMER_1, "c2": cc.CONSUMER_2, "operator": OPERATOR},
        "orgs": {"c1": cc.personal_org(conn, cc.CONSUMER_1),
                 "c2": cc.personal_org(conn, cc.CONSUMER_2)},
        "keys": {"c1": ca.C1_KEY, "c2": ca.C2_KEY, "c2_operator": ca.OPERATOR_KEY},
        "unknown_request": str(request.request_id),
        "drift_wallet": DRIFT_WALLET,
    }


def read_back(conn) -> list[str]:
    """What the Node suite cannot see through PostgREST: the durable effects, exactly."""
    problems = []
    set_drift(conn, 0)           # repair the seeded drift; nothing else may drift
    cs.assert_no_drift(conn, "the U3 world")
    actors = conn.execute(
        "select distinct actor_principal from infrx.audit_entries "
        "where idempotency_key like 'app-operator:%%' or (action = 'admin_adjust' and "
        "after->>'operation_id' in (select operation_id::text from infrx.credit_ledger "
        "where kind = 'operator_adjustment'))").fetchall()
    if actors != [(f"operator:{OPERATOR}",)]:
        problems.append(f"console writes carry another actor: {actors}")
    adjustments = conn.execute(
        "select count(*), count(distinct operation_id) from infrx.credit_ledger l join "
        "infrx.credit_wallets w using (wallet_id) where w.owner_user_id = %s and "
        "l.kind = 'operator_adjustment'", (cc.CONSUMER_1,)).fetchone()
    print(f"# durable: CONSUMER_1 operator adjustments = {adjustments[0]} "
          f"(distinct operations {adjustments[1]})")
    if adjustments[0] != adjustments[1]:
        problems.append("an operation was applied twice")
    negative = conn.execute("select count(*) from infrx.credit_wallets "
                            "where available < 0").fetchone()[0]
    if negative:
        problems.append(f"{negative} wallet(s) below zero available")
    return problems


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
        run = subprocess.run(("node", "--test", "tests/u/operator-postgrest.test.ts"), cwd=APP,
                             env={**os.environ, "INFRX_U3_STACK": handle.name})
        os.unlink(handle.name)
        with pgharness.connect(DB) as conn:
            problems = read_back(conn)
        for problem in problems:
            print(f"# durable: FAIL {problem}")
        return run.returncode or (1 if problems else 0)
    finally:
        down()


if __name__ == "__main__":
    sys.exit(main())
