#!/usr/bin/env python3
"""L3-SQL (wave-5 LW2, lab-sql; 09-amendment-workstreams §L3, the D slice): the Lab control
plane over 0007's registry on real PostgreSQL - LAB-PUBLISH (the SQL half) and LAB-ACCESS for
`0032_lab_control.sql`, composed with `PgLabControlStore` and read back through the one
catalog (`PgCatalogDirectory`) and admission's pin resolution.

World: test_l2sql_access's (the operator seed: NEMO's Marlin serving version S1, its
ready_private dev revision and its active public revision P1 listed as
`nemostation/marlin-2b` v1; OTHER) plus a second serving version S2 with a draft dev revision
D2 and a proposed public revision P2 carrying an effective card. Each `check_*` is the check a
mutant in `code_mutants_l3sql.py` must break; the rolled-back ones leave nothing behind, the
race commits its own rows.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_l3sql_control.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_control import PgLabControlStore

from . import checks_admission as ca
from . import checks_credit as cc
from . import pgharness
from . import test_l2sql_access as l2

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_l3sql"

NEMO, OTHER = l2.NEMO, l2.OTHER
call, ok, refusal, rolled_back = l2.call, l2.ok, l2.refusal, l2.rolled_back
ALIAS = "nemostation/marlin-2b"
P1, S1, DEV1 = cc.PUBLIC_DEPLOYMENT, cc.SERVING, cc.DEV_DEPLOYMENT
S2 = "d0000006-0000-4000-8000-000000000006"
D2 = "c0000006-0000-4000-8000-000000000006"          # S2's private dev revision (draft)
P2 = "c0000007-0000-4000-8000-000000000007"          # S2's proposed public revision
P3 = "c0000008-0000-4000-8000-000000000008"          # a second proposal of S2
CARD1, CARD2, CARD3 = cc.CARD, "rc_marlin2b_s2", "rc_marlin2b_s2b"
RPCS = ("lab_move_deployment", "lab_approve_publication", "lab_rollback_publication",
        "lab_open_dev_wallet", "lab_control_history")


def seed(conn) -> None:
    l2.seed(conn)
    conn.execute(f"""
    insert into infrx.serving_versions (serving_version_id, model_version_id, model_id,
      provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version,
      runtime_image_ref, engine_options_digest, precision, capability, created_by)
    select '{S2}', model_version_id, model_id, provider_org_id, '2026-10-01', prompt_harness_ref,
           preprocessor_profile_version, runtime_image_ref, engine_options_digest, precision,
           capability, 'ops' from infrx.serving_versions where serving_version_id = '{S1}';
    insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id,
      provider_org_id, environment, serving_version_id, visibility, state, max_input_tokens,
      max_output_tokens, created_by) values
      ('{D2}', '{cc.DEV_ENDPOINT}', '{NEMO}', 'dev', '{S2}', 'private', 'draft', 30720, 2048,
       'dev@nemo'),
      ('{P2}', '{cc.PROD_ENDPOINT}', '{NEMO}', 'prod', '{S2}', 'public', 'proposed_public',
       30720, 2048, 'dev@nemo'),
      ('{P3}', '{cc.PROD_ENDPOINT}', '{NEMO}', 'prod', '{S2}', 'public', 'proposed_public',
       30720, 4096, 'dev@nemo');
    insert into infrx.rate_card_versions (rate_card_version, model_id, deployment_revision_id,
      serving_version_id, input_rate_per_million, output_rate_per_million, effective_at,
      approved_by, provisional) values
      ('{CARD2}', '{cc.MODEL}', '{P2}', '{S2}', 300, 900, '2026-09-01T00:00:00Z', 'ops', false),
      ('{CARD3}', '{cc.MODEL}', '{P3}', '{S2}', 300, 900, '2026-09-01T00:00:00Z', 'ops', false);
    """)


def move(conn, revision: str, state: str, *, provider: str = NEMO, operator: bool = False):
    return ok(conn, "lab_move_deployment", {"provider_org_id": provider,
                                            "deployment_revision_id": revision, "state": state,
                                            "actor": "dev@nemo", "operator": operator,
                                            "reason": "journey"})


def moved(conn, revision: str, state: str, *, provider: str = NEMO,
          operator: bool = False) -> str | None:
    return refusal(conn, "lab_move_deployment", {"provider_org_id": provider,
                                                 "deployment_revision_id": revision,
                                                 "state": state, "actor": "dev@nemo",
                                                 "operator": operator, "reason": "journey"})


def approval(revision: str = P2, *, operator: bool = True, card: str = CARD2,
             provider: str = NEMO, alias: str = ALIAS) -> dict:
    return {"provider_org_id": provider, "deployment_revision_id": revision,
            "public_model_id": alias, "rate_card_version": card, "actor": "ops@infrx",
            "operator": operator, "reason": "P-01 approved"}


def rollback(revision: str = P1, *, operator: bool = True) -> dict:
    return {"public_model_id": ALIAS, "deployment_revision_id": revision, "actor": "ops@infrx",
            "operator": operator, "reason": "regression"}


def listed(conn) -> str:
    """The deployment the alias admits at now (admission's own resolution)."""
    return str(cc.resolve(conn, ALIAS)["deployment_revision_id"])


def state(conn, revision: str) -> str:
    return conn.execute("select state from infrx.deployment_revisions where "
                        "deployment_revision_id = %s", (revision,)).fetchone()[0]


def validated(conn) -> None:
    move(conn, D2, "validating")
    move(conn, D2, "ready_private")


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads the audit or executes the control RPCs (a provider
    member included: the Lab reaches them only through the server); the platform role reads
    the audit and writes it only through the RPCs."""
    probes = ["select count(*) from infrx.lab_control_events"]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached the control plane: {reached}"
    assert cc.refused_as(conn, "service", probes[0]) is None, "service cannot read the audit"
    got = cc.refused_as(conn, "service", "delete from infrx.lab_control_events")
    assert got is not None and got.startswith("42501"), f"service edits the audit: {got}"
    assert conn.execute("select relrowsecurity from pg_class where oid = "
                        "'infrx.lab_control_events'::regclass").fetchone()[0]
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused"


@rolled_back
def check_providers_move_only_their_own_private_revisions(conn) -> str:
    """LAB-ACCESS / LAB-PUBLISH: the provider validates its own private dev revision (each
    move audited with actor and states); another provider's move is `not_found`; a provider
    cannot drain or retire a public revision, nor activate anything (`forbidden`); a move
    0007 does not allow is `state_conflict`; an unattributed move is refused."""
    first = move(conn, D2, "validating")
    assert (first["from_state"], first["to_state"], first["action"], first["by_operator"]) == \
        ("draft", "validating", "move", False), first
    assert moved(conn, D2, "ready_private", provider=OTHER) == "not_found"
    assert state(conn, D2) == "validating"
    assert moved(conn, D2, "draft") == "state_conflict", "a move 0007 forbids"
    assert moved(conn, D2, "draining") == "state_conflict", "a private revision drained"
    move(conn, D2, "ready_private")
    got = {s: moved(conn, P1, s) for s in ("draining", "retired", "active")}
    got["activate_proposal"] = moved(conn, P2, "active", operator=True)
    assert got == dict.fromkeys(got, "forbidden"), got
    assert state(conn, P1) == "active" and state(conn, P2) == "proposed_public"
    assert refusal(conn, "lab_move_deployment", {"provider_org_id": NEMO,
                                                 "deployment_revision_id": D2,
                                                 "state": "retired", "actor": "",
                                                 "reason": "x"}) == "invalid_request"
    history = ok(conn, "lab_control_history", {"provider_org_id": NEMO,
                                               "deployment_revision_id": D2})
    assert [(e["from_state"], e["to_state"], e["actor"]) for e in history] == \
        [("draft", "validating", "dev@nemo"), ("validating", "ready_private", "dev@nemo")], history
    assert ok(conn, "lab_control_history", {"provider_org_id": OTHER}) == []
    move(conn, P2, "retired")                                   # a provider withdraws its proposal
    return "own private moves audited; foreign not_found; public moves forbidden; 0007 guards"


@rolled_back
def check_an_operator_approves_a_validated_proposal(conn) -> str:
    """LAB-PUBLISH: a provider proposes, an operator approves - a provider's approval is
    `forbidden`; approval needs the serving version's dev revision validated and an
    effective card of the revision; then the revision is active and the alias's NEXT
    listing version, audited, and discovery (the one catalog) and admission both resolve
    the alias to it."""
    assert refusal(conn, "lab_approve_publication", approval(operator=False)) == "forbidden"
    assert refusal(conn, "lab_approve_publication", approval(provider=OTHER)) == "not_found"
    assert refusal(conn, "lab_approve_publication", approval()) == "state_conflict", \
        "published without a validated dev revision"
    validated(conn)
    assert refusal(conn, "lab_approve_publication", approval(card=CARD1)) == "invalid_request"
    conn.execute("update infrx_test.clock set offset_s = offset_s - interval '3650 days'")
    assert refusal(conn, "lab_approve_publication", approval()) == "invalid_request", \
        "listed at a card not yet in effect"
    conn.execute("update infrx_test.clock set offset_s = offset_s + interval '3650 days'")
    assert refusal(conn, "lab_approve_publication", approval(alias="other/model")) == \
        "invalid_request"
    event = ok(conn, "lab_approve_publication", approval())
    assert (event["action"], event["to_state"], event["listing_version"], event["by_operator"]) \
        == ("approve", "active", 2, True), event
    assert state(conn, P2) == "active" and listed(conn) == P2
    assert refusal(conn, "lab_approve_publication", approval()) == "state_conflict", \
        "an approval replayed as a second listing"
    return "provider cannot approve; needs validated dev + effective card; next listing version"


@rolled_back
def check_dev_revisions_never_reach_app_discovery(conn) -> str:
    """LAB-PUBLISH: a private dev revision - even validated - is never approved, never listed
    and never admitted by a consumer alias; only its provider_dev endpoint names it."""
    validated(conn)
    assert refusal(conn, "lab_approve_publication", approval(D2, card=CARD2)) == \
        "state_conflict"
    got = cc.attempt(conn, "select infrx.lab_list_alias(%s, %s, %s, 'ops', 'x')",
                     (D2, ALIAS, CARD2))
    assert got is not None and "invalid_request" in got, got
    for alias in ("nemostation/marlin-2b-dev", "nemostation/marlin-2b@2026-10-01"):
        got = cc.attempt(conn, "select * from infrx.resolve_admission_pins(%s)", (alias,))
        assert got is not None and "not_found" in got, f"{alias} admitted: {got}"
    assert listed(conn) == P1
    return "dev revisions: no approval, no listing, no consumer discovery"


@rolled_back
def check_rollback_is_a_new_listing_and_admitted_jobs_keep_their_pins(conn) -> str:
    """LAB-PUBLISH / ROLLOUT-PIN: a job admitted before a publication keeps its revision and
    card through the alias switch and the rollback; the rollback is an operator's new
    listing version naming the earlier, still active revision at the card it was listed
    with; a revision never listed under the alias, the current one, or a drained one is not
    a rollback target."""
    world = ca.World(conn)
    org = cc.personal_org(conn, cc.CONSUMER_1)

    def admitted() -> tuple:
        request = ca.credit_request(world, ca.C1_KEY, org, model=ALIAS)
        ca.admit(conn, request, b.idem(request, request.request_id), regime="credit")
        return request.request_id, conn.execute(
            "select deployment_revision_id::text, rate_card_version from infrx.jobs "
            "where request_id = %s", (request.request_id,)).fetchone()
    before, pins = admitted()
    assert pins == (P1, CARD1), pins
    validated(conn)
    ok(conn, "lab_approve_publication", approval())
    during, pins2 = admitted()
    assert pins2 == (P2, CARD2), pins2
    assert refusal(conn, "lab_rollback_publication", rollback(operator=False)) == "forbidden"
    assert refusal(conn, "lab_rollback_publication", rollback(P2)) == "state_conflict"
    assert refusal(conn, "lab_rollback_publication", rollback(DEV1)) == "not_found"
    conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                 "deployment_revision_id, serving_version_id, input_rate_per_million, "
                 "output_rate_per_million, effective_at, approved_by, provisional) values "
                 "('rc_marlin2b_repriced', %s, %s, %s, 999, 999, '2026-09-02', 'ops', false)",
                 (cc.MODEL, P1, S1))
    event = ok(conn, "lab_rollback_publication", rollback())
    assert (event["action"], event["listing_version"], event["deployment_revision_id"]) == \
        ("rollback", 3, P1), event
    assert listed(conn) == P1
    rows = conn.execute("select version, deployment_revision_id::text, rate_card_version "
                        "from infrx.catalog_listings where public_model_id = %s order by version",
                        (ALIAS,)).fetchall()
    assert rows == [(1, P1, CARD1), (2, P2, CARD2), (3, P1, CARD1)], rows
    kept = conn.execute("select request_id::text, deployment_revision_id::text, "
                        "rate_card_version from infrx.jobs where request_id in (%s, %s) "
                        "order by admitted_at", (before, during)).fetchall()
    assert kept == [(before, P1, CARD1), (during, P2, CARD2)], kept
    move(conn, P2, "draining", operator=True)
    assert refusal(conn, "lab_rollback_publication", rollback(P2)) == "state_conflict"
    return "rollback = listing v3 at P1's card; jobs keep P1/P2 pins; drained is no target"


@rolled_back
def check_the_dev_wallet_opens_at_zero_and_is_funded_only_by_audited_allocation(conn) -> str:
    """platforms/02-credits: a provider's provider_dev wallet opens at 0 (again: the same
    wallet), is funded only by an audited operator allocation (D5's `grant_credit`), and a
    provider that does not exist gets none."""
    wallet = ok(conn, "lab_open_dev_wallet", {"provider_org_id": OTHER})
    assert (wallet["kind"], wallet["ledger_total"], wallet["unit"]) == \
        ("provider_dev", "0.00000000", "CREDIT"), wallet
    assert ok(conn, "lab_open_dev_wallet", {"provider_org_id": OTHER}) == wallet
    nemo = ok(conn, "lab_open_dev_wallet", {"provider_org_id": NEMO})
    assert nemo["wallet_id"] == cc.PROVIDER_WALLET, "a second dev wallet for NEMO"
    assert refusal(conn, "lab_open_dev_wallet", {"provider_org_id": l2.NOBODY}) == "not_found"
    grant = ok(conn, "grant_credit", {"wallet_id": wallet["wallet_id"],
                                      "kind": "operator_allocation", "amount": "250",
                                      "operation_id": "0f000000-0000-4000-8000-00000000000f",
                                      "actor": "ops@infrx", "reason": "preview budget",
                                      "at": "2026-09-27T00:00:00Z"})
    assert grant["replayed"] is False
    audit = conn.execute("select actor_principal, action from infrx.audit_entries where "
                         "idempotency_key = 'grant_credit:0f000000-0000-4000-8000-00000000000f'"
                         ).fetchall()
    assert audit == [("ops@infrx", "admin_adjust")], audit
    assert ok(conn, "lab_open_dev_wallet", {"provider_org_id": OTHER})["ledger_total"] == \
        "250.00000000"
    return "opens at 0, idempotent, funded by the audited allocation only"


def _race(conn, fn: str, calls: list[dict]) -> list:
    """Each call from its own connection, released together: its listing version or code."""
    gate, answers = threading.Barrier(len(calls)), [None] * len(calls)

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, fn, calls[i])["listing_version"]
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return sorted(map(str, answers))


def check_concurrent_publications_take_consecutive_versions(conn) -> str:
    """LAB-PUBLISH races (commit their own rows): two approvals of one alias at once both
    land as consecutive listing versions; two rollbacks to the same revision at once land
    once, the other finding it already listed."""
    with conn.transaction():
        validated(conn)
    approvals = _race(conn, "lab_approve_publication",
                      [approval(), approval(P3, card=CARD3)])
    assert approvals == ["2", "3"], approvals
    rollbacks = _race(conn, "lab_rollback_publication", [rollback(), rollback()])
    assert rollbacks == ["4", "state_conflict"], rollbacks
    return f"approvals {approvals}; rollbacks {rollbacks}"


def check_the_store_composes(conn) -> str:
    """`PgLabControlStore` over the RPCs: open, move, history and typed refusals."""
    store = PgLabControlStore(connector(pgharness.dsn(conn.info.dbname)))

    async def go() -> list:
        got = [(await store.open_dev_wallet(NEMO))["kind"]]
        try:
            await store.move(P1, "draining", provider_org_id=NEMO, actor="dev", reason="x")
        except errors.Forbidden:
            got.append("forbidden")
        try:
            await store.rollback(ALIAS, DEV1, actor="ops", reason="x")
        except errors.NotFound:
            got.append("not_found")
        await store.move(D2, "retired", provider_org_id=NEMO, actor="dev", reason="done")
        got.append([e["to_state"] for e in await store.history(NEMO, D2)][-1:])
        return got
    got = asyncio.run(go())
    assert got == ["provider_dev", "forbidden", "not_found", ["retired"]], got
    return f"store round trip: {got}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_providers_move_only_their_own_private_revisions,
    check_an_operator_approves_a_validated_proposal,
    check_dev_revisions_never_reach_app_discovery,
    check_rollback_is_a_new_listing_and_admitted_jobs_keep_their_pins,
    check_the_dev_wallet_opens_at_zero_and_is_funded_only_by_audited_allocation,
    check_concurrent_publications_take_consecutive_versions, check_the_store_composes)}


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
def test_l3sql(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
