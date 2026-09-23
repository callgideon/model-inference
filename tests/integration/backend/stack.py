"""E3B phase 1: what the backend gate adds on top of E2's layer-2 stack.

E2's `infrx-e2` stack already runs PostgreSQL (migrations 0001-0005), Valkey (the selected
queue mode, driven through Q2's `ValkeyScheduler`) and S3-compatible storage. The backend
profile adds the one service it lacks, a pinned **PostgREST** (`backend/compose.yaml`,
project `infrx-e3b`), attached to E2's network. Nothing here starts a GPU, a Next.js app or
anything hosted.

Three things live here, each small:

* `PENDING` - the only vocabulary a pending case may use. A case that cannot run today skips
  with `PENDING[<ids>]`, every id must be a task or P-input of `research/plan`, and
  `run.py --layer 3` counts those skips as pending, never as passes.
* `provision_two_tenants` - the two-tenant fixture. **G6B call site**: today it is built on
  the contracts-v2 fakes (`conformance/v2_fakes.py`) plus the v1 fake JobStore's grant hook;
  when G6B's operator adapter merges, this one function is what switches to it.
* `postgrest_*` - lifecycle of `infrx-e3b-postgrest`, ownership by label exactly like E2's.
"""
from __future__ import annotations

import asyncio
import atexit
import importlib.util
import itertools
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import harness                                          # noqa: E402

# `infrx` normally comes from the checkout. The E3B mutants (tests/integration/mutants.py)
# run the backend suite against a *copy* of `infrx` through PYTHONPATH, and
# `api_on_path()` would put the real one in front of it, so only add it when nothing else
# provides the package.
if importlib.util.find_spec("infrx") is None:
    harness.api_on_path()

# ------------------------------------------------------------------ pending vocabulary

# Every id a pending case may name, with what it delivers. A typo is a failure, not a new
# kind of pending: `pending()` refuses an id that is not here. E3B phase 2: no merged task
# (tasks.json implemented/integrated) is a blocker of an E3B case; the ones still listed are
# `RESIDUAL`, kept only for I3B's recovery cases, which are read-only here and extend this
# vocabulary (`recovery/recoverykit.PENDING`). `test_stage.py` holds both lists to tasks.json.
PENDING = {
    "G2": "synchronous chat, the persistent SSE relay and the cutover composition that "
          "mounts the metered ingress in gateway.app.ROUTERS",
    "G3": "explicit async job routes: create, status, cancel, replay",
    "G4U": "owned upload HTTP adapter",
    "D4": "persistent stream journal in PostgreSQL: infrx.append, replay, PgStreamStore (0017)",
    "D5": "terminal settlement (infrx.terminalize after the fence), grant_credit, operator "
          "adjust/reconcile, and the PostgreSQL adapters of G6B's TenantStore/AuditLog/"
          "Registry/AccountView and G1R's CatalogDirectory",
    "F2P": "wire-in merged (CreditJobStore port + credit fake)",
    # RESIDUAL (merged; I3B's cases only - see RESIDUAL)
    "G1R": "pilot ingress mounted in gateway.app.ROUTERS (cutover from the legacy chat "
           "route) with consumer/provider audiences",
    "D2": "atomic admission RPC: job + hold + reservations + outbox in one transaction",
    "D3": "fenced leases, recovery and cancellation RPCs in PostgreSQL",
    "M3": "owned uploads, expiry and orphan collection",
    "W3": "worker wiring: drain, engine pin, media root, measured concurrency",
}
# Merged tasks still in the vocabulary, and why. Integration request #2 asks I3B to rename
# its blockers; E3B's own cases may not name these (`pending()` refuses them).
RESIDUAL = {
    "G1R": "I3B rc03 (recovery/test_recovery.py) still names it; the mount is G2's cutover",
    "D2": "I3B rc04 names it through stack.unimplemented_rpcs(), which counts D6's permanent "
          "stubs too; the adapter exists (dr01-dr04/dr09 [postgres] run on it)",
    "D3": "I3B rc04, as D2",
    "M3": "I3B rc05b names it for an S3-backed ObjectStore, which no task owns yet",
    "W3": "I3B rc08b names it for a worker process entry point (with I2B)",
}


def pending(*ids: str, why: str):
    """Skip as PENDING. Never a pass: `run.py --layer 3` counts it, and the stage exits 3."""
    import pytest
    unknown = [task for task in ids if task not in PENDING or task in RESIDUAL]
    if not ids or unknown:
        raise AssertionError(f"a pending case must name known unblocking ids, got {ids}")
    pytest.skip(f"PENDING[{','.join(ids)}] {why}")


def ingress_is_mounted() -> bool:
    """The probe every journey case runs first: is the pilot ingress (G1R's cutover) the
    router the composition root mounts? Today it is not - `ROUTERS` is the legacy chat
    route - and the day it is, every journey case stops being pending and fails until its
    body is written, rather than passing on an empty body."""
    from infrx.gateway import app as composition
    return any(module.__name__.endswith(".ingress") for module in composition.ROUTERS)


UNIMPLEMENTED_SQL = ("select count(*) from pg_proc p join pg_namespace n on n.oid = "
                     "p.pronamespace where n.nspname = 'infrx' "
                     "and p.prosrc like '%infrx.unimplemented%'")


def unimplemented_rpcs() -> int:
    """How many `infrx` functions are still `infrx.unimplemented` stubs (D2-D5's RPCs).

    Measured on E2's migrated database when the stack is up; without it, read from the
    migrations themselves (a stub is a function body naming `infrx.unimplemented(`), so the
    probe never answers "implemented" just because nothing was there to ask."""
    if harness.load_state() and harness.owned_containers():
        import psycopg
        with psycopg.connect(harness.pg_dsn(), autocommit=True) as conn:
            return conn.execute(UNIMPLEMENTED_SQL).fetchone()[0]
    return sum(path.read_text().count("perform infrx.unimplemented(")
               for path in harness.MIGRATIONS_DIR.glob("*.sql"))


# E3B phase 2, item 1: the per-drill probe. D6's three stubs (0004) never go away in
# backend-first scope, so a global count pends every drill for ever; a drill pends only while
# one of the functions IT drives is a stub, on the task that stub names.
STUBS_SQL = ("select p.proname, p.prosrc from pg_proc p join pg_namespace n on n.oid = "
             "p.pronamespace where n.nspname = 'infrx' "
             "and p.prosrc like '%infrx.unimplemented(%'")
STUB_OWNER = re.compile(r"infrx\.unimplemented\('[^']*',\s*'(\w+)'\)")


def stub_owners() -> dict[str, str]:
    """rpc -> the task its stub names, for every `infrx` function still an
    `infrx.unimplemented` stub, measured on this stack's migrated database."""
    import psycopg
    with psycopg.connect(harness.pg_dsn(), autocommit=True) as conn:
        rows = conn.execute(STUBS_SQL).fetchall()
    return {name: STUB_OWNER.search(source).group(1) for name, source in rows}


def stubbed(rpcs, owners: dict[str, str] | None = None) -> dict[str, str]:
    """The stubs among the functions a drill drives, with their owners (empty = none)."""
    owners = stub_owners() if owners is None else owners
    return {rpc: owners[rpc] for rpc in rpcs if rpc in owners}


def has_stack() -> bool:
    return bool(harness.load_state() and harness.owned_containers())


# ------------------------------------------------------------------ the real JobStore

# E3B phase 2, item 2 (D2 req 1 / D3 req 6): `pgtesting.make_jobstore_factory` on THIS
# stack's PostgreSQL. A template database carries every migration `pgstate` applies (D4's
# 0017 arrives without an edit), the test clock, D's builders' world and the PROVISIONAL
# Marlin seed (P-01: never a price); each call clones it (`infrx_<ns>_*`, which the clock
# gate needs). Built once per process and dropped at exit with its clones.
TEMPLATE = f"{harness.PG_DATABASE}_tmpl"
_clones = itertools.count(1)
_made: list[str] = []
_factory = None


def _admin(statement: str) -> None:
    import psycopg
    with psycopg.connect(harness.pg_dsn(harness.PG_TEMPLATE_SOURCE), autocommit=True) as conn:
        conn.execute(statement)


def _template() -> None:
    import pgstate
    import psycopg

    from infrx.state import migrations, pgtesting
    harness.provision_database(TEMPLATE)
    # GoTrue's own `auth.users` columns, which every hosted project has and the pinned
    # image's bare auth schema lacks (A1 derives verification from `email_confirmed_at`).
    # `postgres` does not own `auth.users` there, so as `supabase_admin` over the socket.
    harness.run(["docker", "exec", "-i", harness.assert_ours(harness.container_of("postgres")),
                 "psql", "-U", harness.PG_ADMIN_ROLE, "-d", TEMPLATE, "-v", "ON_ERROR_STOP=1",
                 "-c", "alter table auth.users add column if not exists email_confirmed_at "
                       "timestamptz, add column if not exists deleted_at timestamptz"],
                timeout=120.0)
    with psycopg.connect(harness.pg_dsn(TEMPLATE), autocommit=True) as conn:
        pgstate.apply_migrations(conn)
        pgstate.install_test_clock(conn)
        pgtesting.seed(conn)
        conn.execute(migrations.SEED_MARLIN.read_text())
    atexit.register(_drop_all)


def _drop_all() -> None:
    for name in [*_made, TEMPLATE]:
        try:
            _admin(f'drop database if exists "{name}" with (force)')
        except Exception:                          # noqa: BLE001 - the stack may be gone
            pass


def fresh_database() -> str:
    """A clone of the template, unique across processes; at most 12 kept per process."""
    name = f"{harness.PG_DATABASE}_{os.getpid()}_{next(_clones)}"
    _admin(f'create database "{name}" template "{TEMPLATE}"')
    _made.append(name)
    while len(_made) > 12:
        _admin(f'drop database if exists "{_made.pop(0)}" with (force)')
    return name


def current_database() -> str:
    """The clone the last `pg_jobstore()` built (a live drill applies its defect there)."""
    return _made[-1]


class _NoStream:
    """Stands in for the `stream` hook until D4's PgStreamStore gives pgtesting one: a drill
    that reaches it although its `append` is no stub FAILS by name, never by KeyError."""

    def __getattr__(self, name):
        import pytest
        pytest.fail(f"stream.{name}: no PostgreSQL StreamStore on this base (D4's hook)")


def pg_jobstore(limits=None):
    """A fresh real-store conformance Harness (`pgtesting`), on this stack."""
    global _factory
    import pytest
    if not has_stack():
        pytest.skip(f"no {harness.PROJECT} stack: run `tests/integration/run.py --layer 3`")
    if _factory is None:
        from infrx.state import pgtesting
        _template()
        _factory = pgtesting.make_jobstore_factory(fresh_database, harness.pg_dsn)
    h = _factory(limits=limits)
    h.extra.setdefault("stream", _NoStream())
    return h


def defect(sql: str) -> None:
    """A live defect drill on the CURRENT clone only - never the template, never E2's
    database. The store opens its own connection per operation, so a replacement it must
    see is committed; the clone is disposable and dropped with the others."""
    name = current_database()
    if not name.startswith(f"{harness.PG_DATABASE}_{os.getpid()}_"):
        raise AssertionError(f"refusing a defect outside this process's clones: {name}")
    import psycopg
    with psycopg.connect(harness.pg_dsn(name), autocommit=True) as conn:
        conn.execute(sql)


def connect():
    """An autocommit connection to the current clone, as its owner (hooks and drills)."""
    import psycopg
    return psycopg.connect(harness.pg_dsn(current_database()), autocommit=True)


# ------------------------------------------------------------------ CREDIT individuals

# The PROVISIONAL Marlin seed's fixed identities (`infrx/state/seed_marlin_provisional.sql`;
# P-01: its card is never a price). The alias is the public listing; the dev deployment is
# private (R70: never listed).
CREDIT_ALIAS = "nemostation/marlin-2b"
SEED_PROVIDER_ORG = "b0000001-0000-4000-8000-000000000001"
SEED_DEV_ENDPOINT = "c0000001-0000-4000-8000-000000000001"
SEED_DEV_DEPLOYMENT = "c0000003-0000-4000-8000-000000000003"
SEED_PUBLIC_DEPLOYMENT = "c0000004-0000-4000-8000-000000000004"
SEED_MODEL = "d0000001-0000-4000-8000-000000000001"
SEED_SERVING = "d0000003-0000-4000-8000-000000000003"
SEED_CARD = "rc_marlin2b_2026_09_provisional"
SIGNUP_GRANT = Decimal("10000")


@dataclass(frozen=True)
class Individual:
    name: str
    user_id: str
    org_id: str              # the personal organization 0001's signup trigger created
    key_id: str              # a consumer key filed in that organization
    wallet_id: str           # the CREDIT wallet A1's grant created and funded


def seed_individual(conn, name: str) -> tuple[str, str]:
    """A verified individual through `auth.users` (E2's pattern: the 0001 trigger makes the
    profile, the personal organization and the owner membership); returns (user, org)."""
    user = _uuid(f"{name}/user")
    conn.execute("insert into auth.users (id, email, email_confirmed_at) "
                 "values (%s, %s, infrx.now())", (user, f"{name}@e3b2.invalid"))
    org, = conn.execute("select org_id from public.org_members where user_id = %s",
                        (user,)).fetchone()
    return user, str(org)


def enable(conn, *flags: str) -> None:
    conn.execute("update infrx.feature_flags set enabled = true, updated_by = 'e3b2', "
                 "reason = 'e3b2 local drill' where name = any(%s)", (list(flags),))


def credit_world(names=("alpha", "beta")) -> dict[str, Individual]:
    """On the current clone: signup and CREDIT admission switched on, and per name one
    verified individual granted through A1's `public.claim_signup_grant` (10,000 CREDIT,
    once) with one consumer key in its personal organization."""
    world = {}
    with connect() as conn:
        enable(conn, "signup_grant", "credit_admission")
        for name in names:
            user, org = seed_individual(conn, name)
            status, wallet = conn.execute(
                "select status, wallet_id from public.claim_signup_grant(%s, '', null)",
                (user,)).fetchone()
            assert status == "granted", (name, status)
            key = _uuid(f"{name}/credit-key")
            conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, "
                         "key_hash, audience) values (%s, %s, %s, 'k', 'sk-infrx-e3b2cred', "
                         "%s, 'consumer')", (key, org, user, f"hash-{key}"))
            world[name] = Individual(name, user, org, key, str(wallet))
    return world


def function_source(name: str, signature: str) -> str:
    """`pg_get_functiondef` of a function on the current clone (what a defect edits)."""
    import psycopg
    with psycopg.connect(harness.pg_dsn(current_database()), autocommit=True) as conn:
        return conn.execute("select pg_get_functiondef(%s::regprocedure)",
                            (f"{name}({signature})",)).fetchone()[0]


# ------------------------------------------------------------------ two tenants

@dataclass(frozen=True)
class Tenant:
    name: str
    user_id: str
    org_id: str
    key_id: str
    auth: object          # contracts.v2 AuthContextV2 (consumer audience)
    auth_v1: object       # contracts v1 AuthContext for the v1 JobStore ports
    wallet: object        # contracts.v2 WalletRef, RESOLVED from the auth context
    pins: object          # contracts.v2 AdmissionPins for the published Marlin deployment
    provisioned_by: str


def _uuid(name: str) -> str:
    """Deterministic, and a UUIDv4 in shape (the records refuse any other version)."""
    return str(uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_URL, f"infrx-e3b/{name}").bytes,
                         version=4))


def provision_two_tenants(jobs=None, *, grant: str = "5") -> tuple[Tenant, Tenant]:
    """Two consumer tenants, each with a user, a personal org, a scoped key, a wallet
    resolved from the credential (R66) and admission pins for the public Marlin deployment.

    G6B CALL SITE. The operator adapter does not exist on this base, so identities come
    from the v2 fakes and are recorded as `provisioned_by = "v2-fakes (G6B pending)"`. The
    replacement is this function only; the journeys never build a tenant any other way.

    `jobs` is a v1 fake JobStore (or, later, the real one): the grant goes through its
    `grant` hook, the one way a test gives an org balance - never a direct wallet edit.
    `grant` is in the v1 pilot regime's USD-shaped numbers; CREDIT is D1R's (R64/R65: no
    conversion exists, so none is attempted here).
    """
    from infrx.contracts import records as v1
    from infrx.contracts.conformance import v2_fakes
    from infrx.contracts.v2 import fixtures as v2fix, ports, records as v2

    directory = v2_fakes.fake_v2_harness()
    tenants = []
    for name in ("alpha", "beta"):
        user_id, org_id, key_id = (_uuid(f"{name}/user"), _uuid(f"{name}/org"),
                                   _uuid(f"{name}/key"))
        directory.wallets.by_user[user_id] = v2.WalletRef(
            wallet_id=_uuid(f"{name}/wallet"), kind=v2.WalletKind.consumer,
            owner_user_id=user_id, personal_org_id=org_id)
        auth = v2.AuthContextV2(audience=v2.CredentialAudience.consumer, org_id=org_id,
                                key_id=key_id, principal=key_id, role=v1.Role.owner,
                                entitlement_version=1, user_id=user_id)

        async def resolve(auth=auth):
            wallet = ports.resolve_wallet(
                auth, await directory.wallets.consumer_wallet_for_user(auth.user_id))
            deployment = await directory.catalog.resolve(
                v2fix.REQUESTED_MODEL, audience=auth.audience, endpoint_id=auth.endpoint_id)
            pins, _card = ports.pin_admission(
                auth=auth, requested_model=v2fix.REQUESTED_MODEL, deployment=deployment,
                serving=await directory.catalog.serving_revision(deployment.serving_version_id),
                rate_card=await directory.catalog.active_rate_card(
                    deployment.deployment_revision_id),
                policy=await directory.catalog.data_access_policy(
                    deployment.deployment_revision_id))
            return wallet, pins

        wallet, pins = asyncio.run(resolve())
        if jobs is not None:
            jobs.grant(org_id, grant)
        tenants.append(Tenant(
            name=name, user_id=user_id, org_id=org_id, key_id=key_id, auth=auth,
            auth_v1=v1.AuthContext(org_id=org_id, key_id=key_id, principal=key_id,
                                   role=v1.Role.owner, entitlement_version=1),
            wallet=wallet, pins=pins, provisioned_by="v2-fakes (G6B pending)"))
    return tenants[0], tenants[1]


# ------------------------------------------------------------------ PostgREST

COMPOSE_FILE = HERE / "compose.yaml"
# E3B phase 2: derived from E2's namespace. The default is phase 1's `infrx-e3b`; any other
# namespace's project must not start with `harness.PREFIX`, or E2's own guard would see this
# container as a foreign one of its project and refuse to provision or tear down.
PROJECT = "infrx-e3b" if harness.NAMESPACE == "e2" else f"{harness.PROJECT}rest"
POSTGREST = f"{PROJECT}-postgrest"
POSTGREST_PORT = harness.PORT_RANGE.start + 30      # E2: 55530 (08 §8), loopback only
CHECKOUT_LABEL = "ai.infrx.e3b.checkout"
# A local literal that exists in no other file and signs only tokens this suite mints.
JWT_SECRET = "infrx-e3b-local-jwt-secret-not-a-real-one-0001"


def postgrest_url() -> str:
    return f"http://127.0.0.1:{POSTGREST_PORT}"


def _checkout() -> str:
    return os.environ.get("INFRX_E3B_CHECKOUT") or str(HERE)


def _compose(*args: str, check: bool = True):
    return harness.run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
                       check=check, timeout=300.0,
                       env={"INFRX_E3B_CHECKOUT": _checkout(), "INFRX_E3B_PROJECT": PROJECT,
                            "INFRX_E3B_POSTGREST_PORT": str(POSTGREST_PORT),
                            "INFRX_E2_PROJECT": harness.PROJECT,
                            "INFRX_E2_DATABASE": harness.PG_DATABASE})


def postgrest_owner() -> str | None:
    """`None` if absent, "ours" if this checkout created it, else the foreign label."""
    import shutil
    if shutil.which("docker") is None:
        return None
    probe = harness.run(["docker", "inspect", POSTGREST, "--format",
                         "{{json .Config.Labels}}"], check=False, timeout=60)
    if probe.returncode != 0:
        return None
    labels = json.loads(probe.stdout.strip() or "null") or {}
    return "ours" if labels.get(CHECKOUT_LABEL) == _checkout() else \
        f"foreign ({labels.get(CHECKOUT_LABEL)!r})"


def postgrest_up() -> str:
    """Start PostgREST on E2's network and wait for it. Refuses a container it did not
    create. PostgREST reads its schema cache at start, so this runs AFTER `migrate`."""
    owner = postgrest_owner()
    if owner not in (None, "ours"):
        raise harness.HarnessError(f"refusing to touch {POSTGREST}: {owner}")
    _compose("up", "-d", "--no-build", "--force-recreate")
    import time

    import httpx
    last = ""
    for _ in range(60):
        try:
            answer = httpx.get(postgrest_url() + "/", timeout=2.0)
            if answer.status_code < 500:
                return answer.headers.get("server", "postgrest")
            last = f"{answer.status_code} {answer.text[:120]}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(1.0)
    raise harness.HarnessError(f"{POSTGREST} not ready: {last}")


def postgrest_down() -> list[str]:
    """Remove exactly our container (it must go before E2's network can)."""
    owner = postgrest_owner()
    if owner is None:
        return []
    if owner != "ours":
        raise harness.HarnessError(f"refusing to remove {POSTGREST}: {owner}")
    _compose("down", "--remove-orphans", check=False)
    if postgrest_owner() is not None:
        raise harness.HarnessError(f"{POSTGREST} survived its teardown")
    return [POSTGREST]


def jwt(role: str, sub: str | None = None) -> str:
    """HS256, stdlib only: a token only this suite's PostgREST accepts."""
    import base64
    import hashlib
    import hmac
    import time

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    claims = {"role": role, "exp": int(time.time()) + 600}
    if sub:
        claims["sub"] = sub
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"
