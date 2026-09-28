#!/usr/bin/env python3
"""WR-LSQ-9 (wave-5 LW4, lab-sql): `PgControlStore`'s `ControlReads` - the four registry reads
L3's `Operations` (the `/lab/v1/control` route) and `Serving` (R2's ServingControl) make - on
real PostgreSQL, on the platform role and on the control service's own login
`infrx_lab_control` (0043's WR-I2L-4; 0044 adds the one read it lacked: the catalog listings).

World: test_l3sql_control's (NEMO's S1 with DEV1 on the dev endpoint and P1 on the prod
endpoint listed as `nemostation/marlin-2b` v1; S2 with D2 on the dev endpoint and P2/P3
proposed on the prod endpoint; OTHER has nothing deployed). The store reads committed rows on
its own connections, so these checks commit (P2's publication); each mutant runs its one check
on a freshly built database.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_l3sql_reads.py
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from infrx.state import migrations
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector
from infrx.state.lab_control import Listing, PgControlStore

from . import pgharness
from . import test_l3sql_control as t
from .test_d8_requests import as_control_login

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_l3reads"
NEMO, OTHER, ALIAS, cc = t.NEMO, t.OTHER, t.ALIAS, t.cc


def _published(conn) -> None:
    """P2 published at the alias's next version (committed), once."""
    if conn.execute("select max(version) from infrx.catalog_listings where public_model_id = %s",
                    (ALIAS,)).fetchone()[0] == 1:
        t.ok(conn, "lab_control_publish", t.publish())


def _answered(coro):
    """The answer; a refusal (a missing grant, a broken query) is the check's failure."""
    try:
        return asyncio.run(coro)
    except Exception as failed:                         # noqa: BLE001 - reported whole
        raise AssertionError(f"refused: {failed!r}") from None


async def _reads(store: PgControlStore) -> dict:
    return {
        "servings": [s.serving_version_id for s in await store.provider_servings(NEMO)],
        "providers": {s.provider_org_id for s in await store.provider_servings(NEMO)},
        "other_servings": await store.provider_servings(OTHER),
        "deployments": [d.deployment_revision_id for d in await store.provider_deployments(NEMO)],
        "other_deployments": await store.provider_deployments(OTHER),
        "prod_alias": await store.endpoint_alias(cc.PROD_ENDPOINT),
        "dev_alias": await store.endpoint_alias(cc.DEV_ENDPOINT),
        "malformed_alias": await store.endpoint_alias("nope"),
        "listings": await store.listing_versions(ALIAS),
        "unknown_listings": await store.listing_versions("other/model")}


EXPECTED = {
    "servings": [t.S1, t.S2], "providers": {NEMO}, "other_servings": [],
    "deployments": [t.DEV1, t.P1, t.D2, t.P2, t.P3], "other_deployments": [],
    "prod_alias": ALIAS, "dev_alias": None, "malformed_alias": None,
    "listings": [Listing(ALIAS, 1, t.P1, t.CARD1), Listing(ALIAS, 2, t.P2, t.CARD2)],
    "unknown_listings": []}


# ----------------------------------------------------------------------------- checks
def check_the_control_reads_are_the_registrys_rows(conn) -> str:
    """WR-LSQ-9 (LAB-ACCESS / LAB-PUBLISH reads): a provider's serving and deployment revisions
    are its own only, oldest first (another provider's list is empty); an endpoint's alias is
    the one a listing of a deployment ON THAT endpoint names (a dev endpoint no listing names
    has none); an alias's listing versions are every version, oldest first, with the revision
    and card each one pins."""
    _published(conn)
    got = _answered(_reads(PgControlStore(connector(pgharness.dsn(conn.info.dbname)))))
    assert got == EXPECTED, got
    return f"{len(got['deployments'])} deployments, {len(got['listings'])} listing versions"


def check_the_control_login_holds_what_operations_reads(conn) -> str:
    """WR-LSQ-9 on `infrx_lab_control` (the factory's `set_role=False` login, WR-I2L-4b): the
    four reads and the card `Operations` shows (`PgCatalogDirectory.active_rate_card`, which
    reads the catalog listings) answer on the login itself, as does `lab_traces`' serving read
    (T3's retention is ClickHouse/S3 only); it still cannot write a listing (publication is
    the CAS RPC's) or read the jobs."""
    _published(conn)
    login = as_control_login(conn.info.dbname)
    got = _answered(_reads(PgControlStore(login)))
    assert got == EXPECTED, got
    card = _answered(PgCatalogDirectory(login).active_rate_card(t.P2))
    assert card is not None and card.rate_card_version == t.CARD2, card

    def as_control(sql: str) -> str | None:
        try:
            with conn.transaction():
                conn.execute("set local session authorization infrx_lab_control")
                conn.execute(sql)
                raise psycopg.Rollback()
        except psycopg.Error as refused:
            return refused.sqlstate
        return None
    with conn.transaction():                    # lab_traces' PgServing.SQL (tip), verbatim
        conn.execute("set local session authorization infrx_lab_control")
        servings = conn.execute("select serving_version_id::text, model_id::text from "
                                "infrx.serving_versions where provider_org_id = %s",
                                (NEMO,)).fetchall()
    assert sorted(s for s, _ in servings) == [t.S1, t.S2], servings
    refused = {"list": "insert into infrx.catalog_listings select * from infrx.catalog_listings "
                       "limit 0",
               "jobs": "select count(*) from infrx.jobs"}
    got = {k: as_control(v) for k, v in refused.items()}
    assert got == dict.fromkeys(refused, "42501"), got
    return "reads + the listed card answer on the login; listing writes refused"


def check_the_router_functions_are_the_runtime_logins_alone(conn) -> str:
    """SR-R1-1 against 0043's login roles: R1's adapter (`PgRoutingReleases`) answers on the
    runtime's own session (`infrx_runtime`, no `set role`), and the control service's login
    reaches none of the three router functions (a Lab login never routes consumer traffic or
    records an assignment)."""
    import uuid

    from infrx.state.lab_rollout import PgRoutingReleases

    def session(role: str):
        async def connect():
            c = await psycopg.AsyncConnection.connect(pgharness.dsn(conn.info.dbname),
                                                      autocommit=True, prepare_threshold=None)
            await c.execute(f"set session authorization {role}")
            return c
        return connect

    class Auth:
        org_id = str(uuid.uuid4())

    async def attempt(role: str):
        store = PgRoutingReleases(session(role))
        try:
            return (await store.active(ALIAS), await store.eligible(str(uuid.uuid4()), Auth()))
        except Exception as failed:                     # noqa: BLE001 - the answer is its type
            return type(failed).__name__
    got = {role: asyncio.run(attempt(role)) for role in ("infrx_runtime", "infrx_lab_control")}
    assert got == {"infrx_runtime": (None, False), "infrx_lab_control": "InsufficientPrivilege"}, got
    return f"runtime answers {got['infrx_runtime']}; the control login is refused"


CHECKS = {c.__name__: c for c in (
    check_the_control_reads_are_the_registrys_rows,
    check_the_control_login_holds_what_operations_reads,
    check_the_router_functions_are_the_runtime_logins_alone)}


def seed(conn) -> None:
    t.seed(conn)


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_l3sql_reads(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
