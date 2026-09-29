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


def check_endpoint_alias_orders_across_aliases_by_time_not_by_either_ones_version(
        conn) -> str:
    """LSQ5-m1 (R195): 0007 lets two different aliases' deployments sit on the SAME endpoint
    over time (nothing FKs an endpoint to one model); `version` is scoped to one alias, so a
    second alias's v1 listing there - published after the first alias's v2 - is the newer
    listing and must win, even though 2 > 1. LSQ5-m2: the fake used to compare each alias's
    OWN latest version number after checking only that ANY of its listings ever touched the
    endpoint - so it could name an alias whose current listing has since moved elsewhere. This
    check pins the oracle both stores must agree on."""
    _published(conn)                                        # ALIAS is now at v2 (still P1)
    # The test clock is frozen (`infrx.now()` is fixed), so a second row in the same second
    # needs an explicit later `created_at` to be unambiguously the newer listing - a real
    # publication a moment later needs none of this.
    later = conn.execute("select infrx.now() + interval '1 second'").fetchone()[0]
    other_model = "e0000015-0000-4000-8000-000000000001"
    other_version = "e0000015-0000-4000-8000-000000000002"
    other_serving = "e0000015-0000-4000-8000-000000000003"
    other_dep = "e0000015-0000-4000-8000-000000000004"
    params = {"m": other_model, "p": NEMO, "v": other_version, "s": other_serving,
              "d": other_dep, "base": cc.MODEL, "base_s": cc.SERVING, "ep": cc.PROD_ENDPOINT,
              "later": later}
    for statement in (
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, model_uuid, provider_org_id) values ('nemostation/second', 's', "
        "'s', 's', 'live', 'https://s.example', 's', 1, 1, 1024, '{text}', '{text}', %(m)s, "
        "%(p)s);"
        "insert into infrx.model_versions (model_version_id, model_id, provider_org_id, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, created_by) select %(v)s, %(m)s, %(p)s, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, 't' from infrx.model_versions "
        "where model_id = %(base)s limit 1;"
        "insert into infrx.serving_versions (serving_version_id, model_version_id, model_id, "
        "provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version, "
        "runtime_image_ref, runtime_image_digest, engine_options_digest, precision, "
        "capability, created_by) select %(s)s, %(v)s, %(m)s, provider_org_id, 'second-1', "
        "prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
        "runtime_image_digest, engine_options_digest, precision, capability, 't' "
        "from infrx.serving_versions where serving_version_id = %(base_s)s;"
        "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
        "provider_org_id, environment, serving_version_id, visibility, state, "
        "max_input_tokens, max_output_tokens, created_by) values (%(d)s, %(ep)s, %(p)s, "
        "'prod', %(s)s, 'public', 'active', 1, 1, 't');"
        "insert into infrx.rate_card_versions (rate_card_version, model_id, "
        "deployment_revision_id, serving_version_id, input_rate_per_million, "
        "output_rate_per_million, effective_at, approved_by, provisional) values "
        "('rc_second_1', %(m)s, %(d)s, %(s)s, 400, 1200, infrx.now(), 't', true);"
        "insert into infrx.catalog_listings (public_model_id, version, model_id, "
        "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
        "approved_by, created_at) values ('nemostation/second', 1, %(m)s, %(d)s, %(s)s, "
        "'rc_second_1', infrx.now(), 't', %(later)s)").split(";"):
        conn.execute(statement, params)
    store = PgControlStore(connector(pgharness.dsn(conn.info.dbname)))
    newest = _answered(store.endpoint_alias(cc.PROD_ENDPOINT))
    assert newest == "nemostation/second", (
        f"the newest listing on {cc.PROD_ENDPOINT} names 'nemostation/second' (v1, published "
        f"after ALIAS's v2), got {newest!r}")
    assert _answered(store.endpoint_alias(cc.DEV_ENDPOINT)) is None, "the dev endpoint is untouched"
    return "the newer alias's v1 outranks the older alias's v2 on a shared endpoint"


def check_endpoint_alias_answers_nothing_for_an_endpoint_the_alias_moved_off(conn) -> str:
    """0-F1/LSQ5-m2: a catalog listing is never deleted or moved (0007 keeps it as history), so
    an alias that republishes to a NEW deployment on a DIFFERENT endpoint leaves its OLD
    listing behind, still (per its own row) naming a deployment on the endpoint it just moved
    off. `_ENDPOINT_ALIAS`/the fake must only count a listing that is ALSO its own alias's
    CURRENT (highest-version) listing - the old endpoint answers nothing once nothing current
    claims it, even though the stale listing is still the newest row ever written there."""
    other_model = "e0000016-0000-4000-8000-000000000001"
    other_version = "e0000016-0000-4000-8000-000000000002"
    other_serving = "e0000016-0000-4000-8000-000000000003"
    alias = "nemostation/moved"
    params = {"m": other_model, "p": NEMO, "v": other_version, "s": other_serving,
              "ep_a": "e0000016-0000-4000-8000-000000000004",
              "ep_b": "e0000016-0000-4000-8000-000000000005",
              "dep_a": "e0000016-0000-4000-8000-000000000006",
              "dep_b": "e0000016-0000-4000-8000-000000000007",
              "base": cc.MODEL, "base_s": cc.SERVING}
    for statement in (
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, model_uuid, provider_org_id) values ('nemostation/moved', "
        "'s', 's', 's', 'live', 'https://s.example', 's', 1, 1, 1024, '{text}', '{text}', "
        "%(m)s, %(p)s);"
        "insert into infrx.model_versions (model_version_id, model_id, provider_org_id, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, created_by) select %(v)s, %(m)s, %(p)s, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, 't' from infrx.model_versions "
        "where model_id = %(base)s limit 1;"
        "insert into infrx.serving_versions (serving_version_id, model_version_id, model_id, "
        "provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version, "
        "runtime_image_ref, runtime_image_digest, engine_options_digest, precision, "
        "capability, created_by) select %(s)s, %(v)s, %(m)s, provider_org_id, 'moved-1', "
        "prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
        "runtime_image_digest, engine_options_digest, precision, capability, 't' "
        "from infrx.serving_versions where serving_version_id = %(base_s)s;"
        "insert into infrx.endpoints (endpoint_id, provider_org_id, name, environment, "
        "created_by) values (%(ep_a)s, %(p)s, 'moved-a', 'prod', 't'), "
        "(%(ep_b)s, %(p)s, 'moved-b', 'prod', 't');"
        "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
        "provider_org_id, environment, serving_version_id, visibility, state, "
        "max_input_tokens, max_output_tokens, created_by) values "
        "(%(dep_a)s, %(ep_a)s, %(p)s, 'prod', %(s)s, 'public', 'active', 1, 1, 't'), "
        "(%(dep_b)s, %(ep_b)s, %(p)s, 'prod', %(s)s, 'public', 'active', 1, 1, 't');"
        "insert into infrx.rate_card_versions (rate_card_version, model_id, "
        "deployment_revision_id, serving_version_id, input_rate_per_million, "
        "output_rate_per_million, effective_at, approved_by, provisional) values "
        "('rc_moved_1', %(m)s, %(dep_a)s, %(s)s, 400, 1200, infrx.now(), 't', true), "
        "('rc_moved_2', %(m)s, %(dep_b)s, %(s)s, 400, 1200, infrx.now(), 't', true);"
        f"insert into infrx.catalog_listings (public_model_id, version, model_id, "
        f"deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
        f"approved_by, created_at) values "
        f"('{alias}', 1, %(m)s, %(dep_a)s, %(s)s, 'rc_moved_1', infrx.now(), 't', infrx.now()), "
        f"('{alias}', 2, %(m)s, %(dep_b)s, %(s)s, 'rc_moved_2', infrx.now(), 't', "
        "infrx.now() + interval '1 second')").split(";"):
        conn.execute(statement, params)
    store = PgControlStore(connector(pgharness.dsn(conn.info.dbname)))
    moved_off = _answered(store.endpoint_alias(params["ep_a"]))
    assert moved_off is None, (
        f"{alias}'s v1 (on {params['ep_a']}) is no longer its current listing (v2 moved to "
        f"{params['ep_b']}); the old endpoint must answer nothing, got {moved_off!r}")
    current = _answered(store.endpoint_alias(params["ep_b"]))
    assert current == alias, f"{alias}'s current (v2) listing is on {params['ep_b']}, got {current!r}"
    return f"{alias} moved off {params['ep_a']}; only {params['ep_b']} (its current listing) answers"


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
    check_endpoint_alias_orders_across_aliases_by_time_not_by_either_ones_version,
    check_endpoint_alias_answers_nothing_for_an_endpoint_the_alias_moved_off,
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
