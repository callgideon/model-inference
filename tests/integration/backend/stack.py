"""E3B: what the backend gate adds on top of E2's layer-2 stack.

E2's stack (`harness.PROJECT`, `infrx-e2` by default, `infrx-e3b2` for this lane) runs
PostgreSQL (every migration), Valkey (the selected queue mode, driven through Q2's
`ValkeyScheduler`) and S3-compatible storage. The backend profile adds the one service it
lacks, a pinned **PostgREST** (`backend/compose.yaml`, project `PROJECT` below), attached to
E2's network. Nothing here starts a GPU, a Next.js app or anything hosted.

What lives here, each small:

* `PENDING`/`RESIDUAL` - the only vocabulary a pending case may use. A case that cannot run
  today skips with `PENDING[<ids>]`, and `run.py --layer 3` counts those skips as pending,
  never as passes. `stubbed()` is the per-drill probe of which store functions are stubs.
* `pg_jobstore()` - the real PostgreSQL JobStore rig (phase 2): `pgtesting`'s factory over a
  migrated, seeded template on this stack, cloned per call; `defect()` edits a clone only.
* `credit_world()` / `provision_two_tenants()` - CREDIT individuals through `auth.users` and
  A1's grant; the two journey tenants through G6B's `Operations` on D5's PostgreSQL adapters
  (every port real, `REAL_PORTS`).
* `postgrest_*` - lifecycle of the PostgREST container, ownership by label exactly like E2's.
"""
from __future__ import annotations

import asyncio
import atexit
import contextlib
import importlib.util
import itertools
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
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
# vocabulary (`recovery/recoverykit.PENDING`), with owner references that are no task
# (`recoverykit.OWNERS`, e.g. `I2B-R4`, `M1-L2`: never an E3B blocker, never stale).
# `test_stage.py` holds all of them to tasks.json.
#
# E3B's own owner references (R3-1): work no task schedules, named by what it is and who
# owns it - never stale, never a task. E3B phase 3: `G2-R1` (the held cutover) is retired, the
# cutover mounted the ingress; `M3-U1` is the coordinator's ruling (the M lane
# `codex/m-pilot-media` fixes it, and its merge retires the reference).
OWNERS = {"M3-U1": "M: the real media staging (MediaStaging.materialize) must resolve "
                   "finalized infrx-upload:upl_… refs from the object store as the contract "
                   "fake does; today it accepts only http(s)/data: sources"}
# E3B phase 3: D5 merged (terminalize, grant_credit, reconcile, the G6B adapters), so it is
# no id here; the cases that pended on it (dr07c, dr07[postgres], every journey) run.
PENDING = {**OWNERS}
# Merged tasks still in the vocabulary, and why. Integration request #2 asks I3B to rename
# its blockers; E3B's own cases may not name these (`pending()` refuses them).
RESIDUAL: dict[str, str] = {}


def pending(*ids: str, why: str):
    """Skip as PENDING. Never a pass: `run.py --layer 3` counts it, and the stage exits 3."""
    import pytest
    unknown = [task for task in ids if task not in PENDING or task in RESIDUAL]
    if not ids or unknown:
        raise AssertionError(f"a pending case must name known unblocking ids, got {ids}")
    pytest.skip(f"PENDING[{','.join(ids)}] {why}")


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
    return _factory(limits=limits)        # D4: pgtesting's `stream` is PgStreamStore


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
    auth: object          # contracts.v2 AuthContextV2 (consumer audience), from the key row
    auth_v1: object       # contracts v1 AuthContext for the v1 JobStore ports
    wallet: object        # contracts.v2 WalletRef, RESOLVED from the auth context
    pins: object          # contracts.v2 AdmissionPins for the published Marlin deployment
    provisioned_by: str
    secret: str | None = field(default=None, repr=False)     # revealed once, never logged


@dataclass(frozen=True)
class Provisioned:
    alpha: Tenant
    beta: Tenant
    ops: object                                              # infrx.operations Operations
    operator_secret: str = field(repr=False)
    grants: dict = field(default_factory=dict)               # name -> grant_initial result
    database: str = ""                                       # the clone they live in


def _uuid(name: str) -> str:
    """Deterministic, and a UUIDv4 in shape (the records refuse any other version)."""
    return str(uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_URL, f"infrx-e3b/{name}").bytes,
                         version=4))


# Which of G6B's ports are real here; `provisioned_by` repeats it on every tenant. E3B phase 3:
# every one - D5's PostgreSQL adapters (`infrx/state/operations.py`, `PgCatalogDirectory`),
# composed exactly as D5's `cli.build_operations` diff does (D5 integration request 4).
REAL_PORTS = ("IdentityDirectory=PgSignup (A1, 0015)", "Ledger=PgLedger (A1 + D5)",
              "TenantStore=PgTenantStore", "AuditLog=PgAuditLog", "Registry=PgRegistry",
              "AccountView=PgAccountView", "WalletDirectory=PgWalletDirectory",
              "CatalogDirectory=PgCatalogDirectory", "JobStore=PgJobStore")


def operations(database: str):
    """G6B's `Operations` on D5's PostgreSQL adapters over `database`, one fresh
    `service_role` connection per operation (`jobstore.connector`)."""
    from datetime import datetime, timezone

    from infrx.operations import service
    from infrx.state import operations as pg
    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.jobstore import PgJobStore, connector
    connect = connector(harness.pg_dsn(database))
    return service.Operations(
        identities=pg.PgSignup(pg._Db(connect)), tenants=pg.PgTenantStore(connect),
        ledger=pg.PgLedger(connect), audit=pg.PgAuditLog(connect),
        registry=pg.PgRegistry(connect), wallets=pg.PgWalletDirectory(connect),
        catalog=PgCatalogDirectory(connect), jobs=PgJobStore(connect),
        accounts=pg.PgAccountView(connect), clock=lambda: datetime.now(timezone.utc))


def provision_two_tenants() -> Provisioned:
    """Two consumer tenants through G6B's `Operations` (G6B handback: "E3B.a provisions its two
    tenants through this CLI" - its service layer, composed as `cli.build_operations` does), on
    a fresh clone of this stack's store. Per tenant: a verified individual seeded through
    `auth.users` (0001's trigger makes the personal organization), then as the operator
    `grant_initial` under an idempotency key (A1's real grant: 10,000 CREDIT, once) and
    `issue_key` (a consumer key row in `public.api_keys`; G6B refuses a key before a metered
    wallet exists, so the grant comes first), then `tenant(secret)`: the balance, the wallet
    resolved from the credential (R66) and the pins a request would be admitted at.

    Every port is real (REAL_PORTS). The operator key is bootstrapped with 0009's
    `bootstrap_operator_key` and a secret minted now: the service cannot mint one, by design.
    CREDIT admission is switched on, so the clone serves the journeys as it stands."""
    from infrx.contracts import records as v1
    from infrx.contracts.conformance import builders as b
    from infrx.contracts.v2 import fixtures as v2fix, ports
    from infrx.operations import service

    h = pg_jobstore()
    operator_secret = service.new_secret()
    with connect() as conn:
        enable(conn, "signup_grant", "credit_admission")
        users = {name: seed_individual(conn, name) for name in ("alpha", "beta")}
        conn.execute("update public.api_keys set revoked_at = infrx.now() "
                     "where audience = 'operator'")
        conn.execute("select infrx.bootstrap_operator_key(%s, 'e3b3 bootstrap', %s, %s, "
                     "'ops@e3b2.invalid', 'E3B3 operator bootstrap')",
                     (b.ORG_B, operator_secret[:service.PREFIX_CHARS],
                      service.hash_key(operator_secret)))
    ops = operations(h.extra["database"])
    label = f"G6B Operations; real: {', '.join(REAL_PORTS)}"

    async def provision():
        operator = await ops.operator(operator_secret)
        made, grants = [], {}
        for name, (user_id, _org) in users.items():
            grants[name] = await operator.grant_initial(
                user_id, idempotency_key=f"e3b2-grant-{name}", reason="E3B2 local drill")
            issued = await operator.issue_key(user_id, f"{name} journey key",
                                              idempotency_key=f"e3b2-key-{name}",
                                              reason="E3B2 local drill")
            session = await ops.tenant(issued.secret)
            wallet = ports.resolve_wallet(session.auth,
                                          await ops.wallets.consumer_wallet_for_user(user_id))
            pins, _card = await session.quote(v2fix.REQUESTED_MODEL)
            made.append(Tenant(
                name=name, user_id=user_id, org_id=issued.org_id, key_id=issued.key_id,
                auth=session.auth,
                auth_v1=v1.AuthContext(org_id=issued.org_id, key_id=issued.key_id,
                                       principal=issued.key_id, role=v1.Role.owner,
                                       entitlement_version=1),
                wallet=wallet, pins=pins, provisioned_by=label, secret=issued.secret))
        return made, grants

    (alpha, beta), grants = asyncio.run(provision())
    return Provisioned(alpha, beta, ops, operator_secret, grants, database=h.extra["database"])


# ------------------------------------------------------------------ the pilot box's environment

GATEWAY_PORT = harness.PORT_RANGE.start + 40        # e3b2: 56740, loopback only


def pilot_env(database: str, workdir: Path, rest_url: str = "", **extra: str) -> dict[str, str]:
    """The pilot box's environment on this stack, by the 08 §5 names: `pilot` mode, the
    clone as `DATABASE_URL`, this namespace's Valkey, the CREDIT regime at the PROVISIONAL
    Marlin card (P-01: a label, never a price), a processing cache and usage log of its own.
    `SUPABASE_URL` and the service-role key name this stack's PostgREST and a service_role
    token only it accepts."""
    (workdir / "cache").mkdir(parents=True, exist_ok=True)
    return {"INFRX_MODE": "pilot", "DATABASE_URL": harness.pg_dsn(database),
            "VALKEY_URL": harness.valkey_url(), "ACCOUNTING_REGIME": "credit",
            "ACTIVE_RATE_CARD_VERSION": SEED_CARD, "MODEL_ID": CREDIT_ALIAS,
            "PROCESSING_CACHE_DIR": str(workdir / "cache"),
            "USAGE_LOG": str(workdir / "usage.jsonl"),
            "SUPABASE_URL": rest_url or postgrest_url(),
            # the name assembled from parts: test_harness's production-pointer guard scans it
            "SUPABASE_SERVICE" "_ROLE_KEY": jwt("service_role", ttl_s=6 * 3600), **extra}


# ------------------------------------------------------------------ PostgREST

COMPOSE_FILE = HERE / "compose.yaml"
# E3B phase 2: derived from E2's namespace. The default is phase 1's `infrx-e3b`; any other
# namespace's project must not start with `harness.PREFIX`, or E2's own guard would see this
# container as a foreign one of its project and refuse to provision or tear down.
PROJECT = "infrx-e3b" if harness.NAMESPACE == "e2" else f"{harness.PROJECT}rest"
POSTGREST = f"{PROJECT}-postgrest"
POSTGREST_PORT = harness.PORT_RANGE.start + 30      # E2: 55530 (08 §8), loopback only
# E3B phase 3: a second PostgREST over the journey clone - PostgREST serves ONE database, and
# the stage's serves E2's - from the same compose file, in a project and on a port of its own.
JOURNEY_PROJECT = f"{PROJECT}j"
JOURNEY_POSTGREST = f"{JOURNEY_PROJECT}-postgrest"
JOURNEY_POSTGREST_PORT = POSTGREST_PORT + 1
CHECKOUT_LABEL = "ai.infrx.e3b.checkout"
# A local literal that exists in no other file and signs only tokens this suite mints.
JWT_SECRET = "infrx-e3b-local-jwt-secret-not-a-real-one-0001"


def postgrest_url(port: int = POSTGREST_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def _checkout() -> str:
    return os.environ.get("INFRX_E3B_CHECKOUT") or str(HERE)


def _compose(*args: str, check: bool = True, project: str = PROJECT,
             port: int = POSTGREST_PORT, database: str = harness.PG_DATABASE):
    return harness.run(["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
                       check=check, timeout=300.0,
                       env={"INFRX_E3B_CHECKOUT": _checkout(), "INFRX_E3B_PROJECT": project,
                            "INFRX_E3B_POSTGREST_PORT": str(port),
                            "INFRX_E2_PROJECT": harness.PROJECT,
                            "INFRX_E2_DATABASE": database})


def postgrest_owner(container: str = POSTGREST) -> str | None:
    """`None` if absent, "ours" if this checkout created it, else the foreign label."""
    import shutil
    if shutil.which("docker") is None:
        return None
    probe = harness.run(["docker", "inspect", container, "--format",
                         "{{json .Config.Labels}}"], check=False, timeout=60)
    if probe.returncode != 0:
        return None
    labels = json.loads(probe.stdout.strip() or "null") or {}
    return "ours" if labels.get(CHECKOUT_LABEL) == _checkout() else \
        f"foreign ({labels.get(CHECKOUT_LABEL)!r})"


def postgrest_up(project: str = PROJECT, port: int = POSTGREST_PORT,
                 database: str = harness.PG_DATABASE) -> str:
    """Start PostgREST on E2's network and wait for it. Refuses a container it did not
    create. PostgREST reads its schema cache at start, so this runs AFTER `migrate`."""
    container = f"{project}-postgrest"
    owner = postgrest_owner(container)
    if owner not in (None, "ours"):
        raise harness.HarnessError(f"refusing to touch {container}: {owner}")
    _compose("up", "-d", "--no-build", "--force-recreate", project=project, port=port,
             database=database)
    import time

    import httpx
    last = ""
    for _ in range(60):
        try:
            answer = httpx.get(postgrest_url(port) + "/", timeout=2.0)
            if answer.status_code < 500:
                return answer.headers.get("server", "postgrest")
            last = f"{answer.status_code} {answer.text[:120]}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(1.0)
    raise harness.HarnessError(f"{container} not ready: {last}")


def postgrest_down(projects: tuple[str, ...] = (JOURNEY_PROJECT, PROJECT)) -> list[str]:
    """Remove exactly our containers (they must go before E2's network can): the stage's,
    and a journey's that a killed run left behind."""
    removed = []
    for project in projects:
        container = f"{project}-postgrest"
        owner = postgrest_owner(container)
        if owner is None:
            continue
        if owner != "ours":
            raise harness.HarnessError(f"refusing to remove {container}: {owner}")
        _compose("down", "--remove-orphans", check=False, project=project)
        if postgrest_owner(container) is not None:
            raise harness.HarnessError(f"{container} survived its teardown")
        removed.append(container)
    return removed


@contextlib.contextmanager
def journey_postgrest(database: str):
    """PostgREST over a journey clone - the gateway's key lookups (`Auth`) read the clone's
    `api_keys` through it, as the pilot's read hosted Supabase's - removed afterwards."""
    postgrest_up(JOURNEY_PROJECT, JOURNEY_POSTGREST_PORT, database)
    try:
        yield postgrest_url(JOURNEY_POSTGREST_PORT)
    finally:
        postgrest_down((JOURNEY_PROJECT,))


def jwt(role: str, sub: str | None = None, *, ttl_s: int = 600) -> str:
    """HS256, stdlib only: a token only this suite's PostgREST accepts."""
    import base64
    import hashlib
    import hmac
    import time

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    claims = {"role": role, "exp": int(time.time()) + ttl_s}
    if sub:
        claims["sub"] = sub
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"
