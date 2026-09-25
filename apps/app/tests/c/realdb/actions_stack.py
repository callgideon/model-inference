#!/usr/bin/env python3
"""C3A (DUR-RLS, CONSOLE-FLOWS): run the App's trusted actions against a REAL task-local stack.

The pinned Supabase PostgreSQL image with every migration applied (the D harness, task
`app-c3a`: container `infrx-app-c3a-postgres-supabase` on 127.0.0.1:55452), the pinned
PostgREST v13.0.4 in this lane's own container and network, signed JWTs for anon,
authenticated individuals and service_role. `tests/c/actions-postgrest.test.ts` drives
`lib/services/actions.ts` through supabase-js exactly as `app/actions.ts` composes it.

    cd apps/infrx-api && INFRX_D_TASK=app-c3a INFRX_D1_IMAGE=supabase \\
        uv run --frozen python ../app/tests/c/realdb/actions_stack.py

Same shape as C0's `stack.py` (whose module pins C0's port, so it is not imported). Everything
is labelled with this checkout and removed at exit; nothing hosted is touched. The JWT secret
and passwords are local, per-container test values.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("INFRX_D_TASK", "app-c3a")
os.environ.setdefault("INFRX_D1_IMAGE", "supabase")
APP = Path(__file__).resolve().parents[3]
API = APP.parent / "infrx-api"
sys.path.insert(0, str(API))

import httpx  # noqa: E402
from infrx.state import migrations  # noqa: E402
from tests.d import checks_admission as ca  # noqa: E402
from tests.d import checks_credit as cc  # noqa: E402
from tests.d import checks_signup  # noqa: E402
from tests.d import pgharness  # noqa: E402

assert pgharness.SERVICE.host_port == 55452 and pgharness.ON_SUPABASE, \
    "run with INFRX_D_TASK=app-c3a INFRX_D1_IMAGE=supabase (this lane's port, the real image)"

POSTGREST = ("postgrest/postgrest@sha256:"
             "a312f4b2e48530a01fc26f5310d547d6c26d087858360e164522e415723a7732")  # v13.0.4
NAME, NETWORK, DB_ALIAS = "infrx-app-c3a-postgrest", "infrx-app-c3a-net", "infrx-app-c3a-db"
LABEL = "ai.infrx.app-c3a.checkout"
JWT_SECRET = "infrx-app-c3a-local-jwt-secret-not-a-real-one"
AUTHN_PASSWORD = "infrx-app-c3a-authenticator-local"
DB = f"{pgharness.DATABASE}_rest"

FRESH = "c3a00000-0000-4000-8000-000000000001"        # verified, never granted
UNVERIFIED = "c3a00000-0000-4000-8000-000000000002"   # signed up, not verified


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


def seed(conn) -> dict:
    ca.seed_admission(conn)      # CONSUMER_1/2 granted, their consumer keys, C2's operator key
    c2_org = cc.personal_org(conn, cc.CONSUMER_2)
    conn.execute("select infrx.set_suspension(%s, true, 'other', 'c3a-test', 'fixture', %s)",
                 (c2_org, f"c3a-suspend-{uuid.uuid4()}"))
    for user in (FRESH, UNVERIFIED):
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"{user[-4:]}@example.com"))
    # GoTrue's verification timestamp is the evidence 0015 reads (through infrx.verified_user);
    # the bare image has no GoTrue columns, so add the hosted ones (as tests/d does).
    checks_signup.gotrue_columns(conn)
    conn.execute("update auth.users set email_confirmed_at = infrx.now() where id = %s", (FRESH,))
    return {
        "users": {"c1": cc.CONSUMER_1, "c2": cc.CONSUMER_2, "shared": cc.SHARED,
                  "fresh": FRESH, "unverified": UNVERIFIED},
        "orgs": {"c1": cc.personal_org(conn, cc.CONSUMER_1), "c2": c2_org,
                 "shared": cc.personal_org(conn, cc.SHARED),
                 "unverified": cc.personal_org(conn, UNVERIFIED)},
        "keys": {"c1": ca.C1_KEY, "c2": ca.C2_KEY, "c2_operator": ca.OPERATOR_KEY},
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
        run = subprocess.run(("node", "--test", "tests/c/actions-postgrest.test.ts"), cwd=APP,
                             env={**os.environ, "INFRX_C3A_STACK": handle.name})
        with pgharness.connect(DB) as conn:
            # Read back the durable state the test could not see through PostgREST.
            grants = conn.execute(
                "select count(*) from infrx.credit_ledger l join infrx.credit_wallets w "
                "using (wallet_id) where w.owner_user_id = %s and l.kind = 'signup_grant'",
                (FRESH,)).fetchone()[0]
            print(f"# durable: signup_grant ledger entries for the fresh individual = {grants}")
            if grants != 1:
                return 1
        os.unlink(handle.name)
        return run.returncode
    finally:
        down()


if __name__ == "__main__":
    sys.exit(main())
