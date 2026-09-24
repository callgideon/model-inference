#!/usr/bin/env python3
"""D10.c on real PostgreSQL (both images): persisted result expiry, the consumer reads as
real JWT principals, P-22's resolved USD price, the listing card as the one authority, the
reconcile race, and the dedicated runtime role (0021 `infrx_runtime`) actually running the
D10 ports and the CREDIT JobStore suite with its own grants - no `set role`.

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_reads.py
    INFRX_D_TASK=d10 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_reads.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts.conformance.lifecycle import cases as lifecycle_cases
from infrx.contracts.conformance.v2_contracts import credit_jobstore_cases
from infrx.state import migrations, pgtesting
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector

from . import checks_admission as ca
from . import checks_credit as cc
from . import checks_reads as cd
from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_reads"
#: A local test credential for the lane's own container (like pgharness.PASSWORD); the
#: operator sets the real one from the secret store, never in a file.
RUNTIME_PASSWORD = "infrx-d10-runtime-local"
_state: dict = {}


def _db():
    if "conn" not in _state:
        pgharness.ensure()
        pgharness.recreate(DB)
        pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(DB)
        ca.seed_admission(conn)
        _state["conn"] = conn
    return _state["conn"]


def test_result_expiry_is_the_persisted_instant() -> None:
    print(cd.check_result_expiry_persisted(_db()))


def test_consumer_reads_as_real_principals() -> None:
    print(cd.check_consumer_reads(_db()))


def test_usd_price_resolves_then_prices() -> None:
    print(cd.check_usd_resolution(_db()))


def test_the_listing_card_is_the_one_authority_in_sql() -> None:
    print(cd.check_listing_card_authority(_db()))


def test_reads_privileges() -> None:
    print(cd.check_reads_privileges(_db()))


def test_a_regime_freeze_waits_for_the_admission_in_flight() -> None:
    _db()
    print(cd.check_flag_freeze_race(pgharness.connect, DB))


def test_reconcile_race() -> None:
    _db()
    print(cd.check_reconcile_race(pgharness.connect, DB))


def test_the_catalog_directory_reads_the_listing_card() -> None:
    """F2C.c finding 2: `PgCatalogDirectory.active_rate_card` (gateway validation and the
    price probe) answers the card the listing names - a newer card minted for the deployment
    does not reprice it (it answered the newest card before D10); a private deployment no
    listing names keeps its newest card."""
    conn = _db()
    catalog = PgCatalogDirectory(connector(pgharness.dsn(DB)))
    conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                 "deployment_revision_id, serving_version_id, input_rate_per_million, "
                 "output_rate_per_million, effective_at, approved_by, provisional) select "
                 "'rc_minted_newer', model_id, deployment_revision_id, serving_version_id, "
                 "5, 5, infrx.now(), 'ops', true from infrx.rate_card_versions where "
                 "rate_card_version = %s on conflict do nothing", (cc.CARD,))

    async def run():
        card = await catalog.active_rate_card(cc.PUBLIC_DEPLOYMENT)
        assert card is not None and card.rate_card_version == cc.CARD, \
            f"a minted card repriced the listing: {card and card.rate_card_version}"
        private = await catalog.active_rate_card(cc.DEV_DEPLOYMENT)
        assert private is not None and private.rate_card_version == cc.DEV_CARD
    asyncio.run(run())


# --------------------------------------------------------------------- the runtime role
def _runtime_connect(name: str):
    return connector(pgharness.dsn(name).replace(
        f"postgres:{pgharness.PASSWORD}@", f"infrx_runtime:{RUNTIME_PASSWORD}@"),
        set_role=False)


def _login_runtime() -> None:
    """Give the (cluster-wide) role a login in the lane's own test container only."""
    pgharness.ensure()
    with pgharness.connect("postgres") as admin:
        admin.execute(f"alter role infrx_runtime login password '{RUNTIME_PASSWORD}'")


_LIFECYCLE = pgtesting.make_lifecycle_factory(
    pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text(),
    connect_for=_runtime_connect)
_CREDIT = pgtesting.make_credit_jobstore_factory(
    pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text(),
    connect_for=_runtime_connect)
# The same pending cases as tests/d/test_credit_jobstore_conformance.py (G1R and a contract
# delta, neither a privilege).
from .test_credit_jobstore_conformance import PENDING as _CREDIT_PENDING  # noqa: E402
from .test_credit_jobstore_conformance import RAISES as _CREDIT_RAISES  # noqa: E402


@pytest.mark.parametrize("case", lifecycle_cases(), ids=lambda c: c.__name__)
def test_runtime_role_runs_the_lifecycle_ports(case) -> None:
    """RV-09 / I8: every D10 port operation succeeds as `infrx_runtime` with its own grants
    (no `set role service_role`, no session settings) - the privilege list is sufficient."""
    _login_runtime()
    asyncio.run(case(_LIFECYCLE))


@pytest.mark.parametrize("case", [
    pytest.param(case, id=case.__name__, marks=[pytest.mark.xfail(
        strict=True, reason=_CREDIT_PENDING[case.__name__],
        raises=_CREDIT_RAISES[case.__name__])] if case.__name__ in _CREDIT_PENDING else [])
    for case in credit_jobstore_cases()])
def test_runtime_role_runs_the_credit_jobstore(case) -> None:
    """The CREDIT JobStore/StreamStore suite as `infrx_runtime`: admission, preparation,
    leases, journal, settlement and reads with only the runtime's grants."""
    _login_runtime()
    asyncio.run(case(_CREDIT))


def test_runtime_role_is_refused_what_it_was_not_granted() -> None:
    """The negative half: the runtime login cannot run an operator operation or read the
    browser ledger (42501), and its statement timeout is its own role default."""
    _login_runtime()
    _db()

    async def run():
        conn = await _runtime_connect(DB)()
        try:
            timeout = await (await conn.execute("show statement_timeout")).fetchone()
            assert timeout == ("15s",), timeout
            for sql in ("select infrx.reconcile('{}'::jsonb)",
                        "select count(*) from public.credit_ledger",
                        "update infrx.credit_wallets set reserved_total = 0"):
                try:
                    await conn.execute(sql)
                except Exception as refused:          # psycopg.errors.InsufficientPrivilege
                    assert getattr(refused, "sqlstate", None) == "42501", (sql, refused)
                else:
                    raise AssertionError(f"infrx_runtime ran {sql}")
        finally:
            await conn.close()
    asyncio.run(run())


def test_the_connector_carries_no_session_state_on_the_transaction_port(monkeypatch) -> None:
    """WR-I8-1 / S3 F6: `connector` never SETs a role on the transaction pooler's port (a
    session SET there is lost for this client and leaks to another), never uses server-side
    prepared statements, and a dedicated login (`set_role=False`) sets nothing anywhere."""
    import psycopg
    seen: list = []

    class Fake:
        async def execute(self, sql, *a):
            seen.append(sql)

    async def fake_connect(dsn, **kw):
        seen.append(("connect", kw.get("prepare_threshold", "default")))
        return Fake()
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", staticmethod(fake_connect))

    async def run(dsn, **kw):
        seen.clear()
        await connector(dsn, **kw)()
        return list(seen)
    assert asyncio.run(run("postgresql://r@h:5432/db")) == [("connect", None),
                                                          "set role service_role"]
    assert asyncio.run(run("postgresql://r@h:6543/db")) == [("connect", None)]
    assert asyncio.run(run("postgresql://r@h:5432/db", set_role=False)) == [("connect", None)]
