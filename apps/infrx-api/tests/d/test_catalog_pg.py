#!/usr/bin/env python3
"""D5 item 8: `PgCatalogDirectory` (infrx/state/catalog.py) against a REAL PostgreSQL (both
images): its own cases, and every exported v2 case on a `V2Harness` whose CATALOG is the
PostgreSQL adapter (the wallet and provider directories stay the fakes, labelled - their
owners are D's unassigned `PgWalletDirectory` port and Lab). A case that cannot run on the
real catalog is partitioned strict-xfail with its reason.

The catalog database is the operator's Marlin seed (the v2 fixtures verbatim) on the
migrated schema, its clock frozen at the v2 cases' `NOW`; each harness is a fresh clone.

    INFRX_D_TASK=d5 uv run --frozen pytest -q tests/d/test_catalog_pg.py
"""
from __future__ import annotations

import asyncio
import itertools
import threading
import uuid

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import v2_contracts
from infrx.contracts.conformance.v2_fakes import NOW, V2Harness, fake_v2_harness
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.records import CredentialAudience
from infrx.state import migrations
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector

from . import checks_admission, checks_credit, pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
TEMPLATE = f"{pgharness.DATABASE}_cattmpl"
IDS = v2fix.IDS
_names = itertools.count(1)
_state: dict = {}


def _template() -> None:
    if _state.get("template"):
        return
    pgharness.ensure()
    pgharness.recreate(TEMPLATE)
    pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(TEMPLATE) as conn:
        conn.execute("select infrx_test.freeze(%s)", (NOW,))
        checks_credit.apply_seed(conn)
    _state["template"] = True


def fresh() -> str:
    _template()
    name = f"{pgharness.DATABASE}_cat_{next(_names)}"
    with pgharness.connect("postgres") as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.execute(f'create database "{name}" template "{TEMPLATE}"')
    return name


class RigCatalog(PgCatalogDirectory):
    """The adapter plus the two TEST hooks the fake catalog has (`publish`, `move_alias`),
    written as an operator does: a new card row, a new listing version. 0007 lists public
    deployments only, so an alias moved onto a private one is refused."""

    def __init__(self, database: str) -> None:
        super().__init__(connector(pgharness.dsn(database)))
        self.owner = pgharness.connect(database)

    def publish(self, card) -> None:
        self.owner.execute("select infrx_test.advance(1)")      # the newer card is newer
        self.owner.execute(
            "insert into infrx.rate_card_versions (rate_card_version, model_id, "
            "deployment_revision_id, serving_version_id, input_rate_per_million, "
            "output_rate_per_million, effective_at, approved_by, provisional) values "
            "(%s, %s, %s, %s, %s, %s, %s, %s, false)",
            (card.rate_card_version, card.model_id, card.deployment_revision_id,
             card.serving_version_id, card.input_rate_per_million.raw("CREDIT"),
             card.output_rate_per_million.raw("CREDIT"), card.effective_at, card.approved_by))

    def move_alias(self, requested_model: str, deployment_revision_id: str) -> None:
        public = self.owner.execute("select visibility = 'public' from "
                                    "infrx.deployment_revisions where deployment_revision_id "
                                    "= %s", (deployment_revision_id,)).fetchone()
        if not (public and public[0]):
            raise errors.InvalidRequest("0007 lists public deployments only")
        from infrx.state.operations import PgRegistry
        asyncio.run(PgRegistry(connector(pgharness.dsn(self.owner.info.dbname)))
                    .move_alias(requested_model, deployment_revision_id))


def factory() -> V2Harness:
    fakes = fake_v2_harness()
    return V2Harness(wallets=fakes.wallets, catalog=RigCatalog(fresh()),
                     providers=fakes.providers, now=NOW)


def run(coro):
    return asyncio.run(coro)


# --- the adapter's own cases ------------------------------------------------------------
def test_credit_rate__private_is_none_to_everyone_else() -> None:
    """R70: the public alias (and `@label`) resolves the listed public deployment for any
    audience; the private dev deployment only for a provider_dev credential on its own
    endpoint - a consumer, an operator, a provider on another endpoint and an unknown
    name all get None, never an error that confirms it exists."""
    catalog = RigCatalog(fresh())
    for model in (v2fix.PUBLIC_MODEL_ID, v2fix.REQUESTED_MODEL):
        found = run(catalog.resolve(model, audience=CredentialAudience.consumer,
                                    endpoint_id=None))
        assert found == v2fix.BUILDERS["deployment_revision_public.json"](), found
    dev = v2fix.DEV_REQUESTED_MODEL
    for audience, endpoint in ((CredentialAudience.consumer, None),
                               (CredentialAudience.consumer, IDS.dev_endpoint),
                               (CredentialAudience.operator, IDS.dev_endpoint),
                               (CredentialAudience.provider_dev, IDS.prod_endpoint),
                               (CredentialAudience.provider_dev, None)):
        assert run(catalog.resolve(dev, audience=audience, endpoint_id=endpoint)) is None, \
            (audience, endpoint)
    mine = run(catalog.resolve(dev, audience=CredentialAudience.provider_dev,
                               endpoint_id=IDS.dev_endpoint))
    assert mine == v2fix.BUILDERS["deployment_revision_private_dev.json"](), mine
    for unknown in ("no/such-model", f"{v2fix.PUBLIC_MODEL_ID}@1999-01-01",
                    f"{v2fix.PUBLIC_MODEL_ID}-dev@2026-09-01"):
        assert run(catalog.resolve(unknown, audience=CredentialAudience.consumer,
                                   endpoint_id=None)) is None, unknown
    assert run(catalog.serving_revision(IDS.serving_version)) == \
        v2fix.BUILDERS["serving_revision.json"]()
    assert run(catalog.serving_revision(str(uuid.uuid4()))) is None


def test_credit_rate__unpriced_answers_none() -> None:
    """R69: the dev deployment has no card (unpriced is unserveable, not free): None; the
    public one answers its approved card; a card not yet effective at the DATABASE clock
    is not active; the policy in force is the seeded one."""
    catalog = RigCatalog(fresh())
    assert run(catalog.active_rate_card(IDS.dev_deployment)) is None
    assert run(catalog.active_rate_card(IDS.prod_deployment)) == \
        v2fix.BUILDERS["rate_card_marlin.json"]()
    catalog.owner.execute(
        "insert into infrx.rate_card_versions (rate_card_version, model_id, "
        "deployment_revision_id, serving_version_id, input_rate_per_million, "
        "output_rate_per_million, effective_at, approved_by, provisional) values "
        "('rc_d5_future', %s, %s, %s, 1, 1, infrx.now() + interval '1 hour', 'ops', false)",
        (IDS.model, IDS.dev_deployment, IDS.serving_version))
    assert run(catalog.active_rate_card(IDS.dev_deployment)) is None, "a future card is active"
    catalog.owner.execute("select infrx_test.advance(3600)")
    assert run(catalog.active_rate_card(IDS.dev_deployment)).rate_card_version == "rc_d5_future"
    assert run(catalog.data_access_policy(IDS.prod_deployment)) == \
        v2fix.BUILDERS["data_access_policy.json"]()
    assert run(catalog.data_access_policy(str(uuid.uuid4()))) is None


def test_credit_rate__alias_move_changes_resolve_not_an_admitted_job() -> None:
    """CREDIT-RATE / R78: a CREDIT job admitted at the listed deployment keeps its pins
    after the alias moves to a new public deployment with its own card - while `resolve`
    and a new admission follow the move."""
    database = f"{pgharness.DATABASE}_cat_admit"
    pgharness.ensure()                           # runnable alone (`-k`), not only in order
    pgharness.recreate(database)
    pgharness.apply(database, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    owner = pgharness.connect(database)
    checks_admission.seed_admission(owner)
    catalog = RigCatalog(database)
    world = checks_admission.World(owner)
    org = checks_credit.personal_org(owner, checks_credit.CONSUMER_1)
    first = checks_admission.credit_request(world, checks_admission.C1_KEY, org)
    checks_admission.admit(owner, first, checks_admission.b.idem(first, "first"),
                           regime="credit")
    moved = str(uuid.uuid4())
    owner.execute("insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
                  "provider_org_id, environment, serving_version_id, visibility, state, "
                  "max_input_tokens, max_output_tokens, created_by) values (%s, %s, %s, 'prod', "
                  "%s, 'public', 'active', 30720, 2048, 'ops')",
                  (moved, IDS.prod_endpoint, IDS.provider_org, IDS.serving_version))
    # review CF-1: two cards on the moved deployment - the alias moves at the NEWEST
    # effective one (the listing's card is what every new admission pins and pays)
    for card, effective in (("rc_d5_moved_old", "infrx.now() - interval '1 hour'"),
                            ("rc_d5_moved", "infrx.now()")):
        owner.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                      "deployment_revision_id, serving_version_id, input_rate_per_million, "
                      "output_rate_per_million, effective_at, approved_by, provisional) "
                      f"values (%s, %s, %s, %s, 500, 1500, {effective}, 'ops', false)",
                      (card, IDS.model, moved, IDS.serving_version))
    catalog.move_alias(v2fix.REQUESTED_MODEL, moved)
    assert owner.execute("select deployment_revision_id::text, rate_card_version from "
                         "infrx.resolve_admission_pins(%s)", (v2fix.REQUESTED_MODEL,)
                         ).fetchone() == (moved, "rc_d5_moved"), "the alias moved at an old card"
    found = run(catalog.resolve(v2fix.REQUESTED_MODEL, audience=CredentialAudience.consumer,
                                endpoint_id=None))
    assert found.deployment_revision_id == moved, found
    pinned = owner.execute("select deployment_revision_id::text, rate_card_version from "
                           "infrx.jobs where request_id = %s", (first.request_id,)).fetchone()
    assert pinned == (IDS.prod_deployment, v2fix.RATE_CARD_VERSION), pinned
    later = checks_admission.credit_request(world, checks_admission.C1_KEY, org)
    checks_admission.admit(owner, later, checks_admission.b.idem(later, "later"),
                           regime="credit")
    assert owner.execute("select deployment_revision_id::text, rate_card_version from "
                         "infrx.jobs where request_id = %s", (later.request_id,)).fetchone() \
        == (moved, "rc_d5_moved")


def test_credit_rate__a_lookup_from_a_fresh_thread_and_loop_is_answered() -> None:
    """G2 request 4: `pilot.Probe` asks its first answer on its own thread; the adapter holds
    no loop-bound pool, so a new thread with a new event loop is answered."""
    catalog = RigCatalog(fresh())
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("card", asyncio.run(
        catalog.active_rate_card(IDS.prod_deployment))))
    thread.start()
    thread.join(30)
    assert out["card"] == v2fix.BUILDERS["rate_card_marlin.json"](), out


def test_credit_rate__a_database_error_is_raised_not_answered_none() -> None:
    """An unreachable or broken store is an error the ingress maps to 503, never None."""
    catalog = PgCatalogDirectory(connector(pgharness.dsn(f"{pgharness.DATABASE}_no_such_db")))
    with pytest.raises(Exception) as raised:
        run(catalog.resolve(v2fix.REQUESTED_MODEL, audience=CredentialAudience.consumer,
                            endpoint_id=None))
    assert not isinstance(raised.value, AssertionError), raised.value


# --- the exported v2 cases on the PostgreSQL catalog -------------------------------------
PENDING = {
    "credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job":
        "0007 lists PUBLIC deployments only: the case moves the public alias onto the private "
        "dev deployment, which catalog_listings' visibility FK refuses (the same invariant on "
        "a public deployment: test_credit_rate__alias_move_changes_resolve_not_an_admitted_job)",
}
RAISES = {"credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job":
          errors.InvalidRequest}


def _param(case):
    reason = PENDING.get(case.__name__)
    marks = [pytest.mark.xfail(strict=True, reason=reason,
                               raises=RAISES[case.__name__])] if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


@pytest.mark.parametrize("case", [_param(case) for case in v2_contracts.cases()])
def test_v2_case_on_the_postgres_catalog(case) -> None:
    asyncio.run(case(factory))
