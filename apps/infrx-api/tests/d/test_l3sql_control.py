#!/usr/bin/env python3
"""L3-SQL (wave-5 LW2, lab-sql; 09-amendment-workstreams §L3, the D slice): the Lab control
plane over 0007's registry on real PostgreSQL - LAB-PUBLISH (the SQL half) and LAB-ACCESS for
`0032_lab_control.sql`, the `ControlStore` seam of the L3 service (WR-L3-1), composed with
`PgControlStore` and read back through the one catalog and admission's pin resolution.

World: test_l2sql_access's (the operator seed: NEMO's Marlin serving version S1, its
ready_private dev revision and its active public revision P1 listed as
`nemostation/marlin-2b` v1; OTHER) plus a second serving version S2 with a draft dev revision
D2 and two proposed public revisions P2/P3 (no card yet: publication inserts it). Each
`check_*` is the check a mutant in `code_mutants_l3sql.py` must break; the rolled-back ones
leave nothing behind, the race commits its own rows.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_l3sql_control.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.v2.money_units import Credit
from infrx.contracts.v2.records import (DeploymentRevision, DeploymentState, Environment,
                                        RateCardSnapshot, Visibility)
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_control import PgControlStore

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
NEW = "c000000a-0000-4000-8000-00000000000a"          # a proposal this file inserts
RETRY_ID = "c000000c-0000-4000-8000-00000000000c"     # a second, freshly-minted retry id
S3 = "d0000009-0000-4000-8000-000000000009"           # a third serving version, for the race
D3 = "c0000009-0000-4000-8000-000000000009"           # its ready_private dev revision
RACE_A = "c000000d-0000-4000-8000-00000000000d"       # two concurrent proposals of D3
RACE_B = "c000000e-0000-4000-8000-00000000000e"
CARD1, CARD2, CARD3 = cc.CARD, "rc_marlin2b_s2", "rc_marlin2b_s2b"
RPCS = ("lab_control_endpoint", "lab_control_transition", "lab_control_propose",
        "lab_control_dev_key", "lab_control_publish", "lab_control_rollback",
        "lab_control_fund", "lab_control_events")


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
    """)


def move(revision: str, expected: str, to: str, *, provider: str = NEMO,
         actor: str = "dev@nemo") -> dict:
    return {"deployment_revision_id": revision, "provider_org_id": provider,
            "expected": expected, "to": to, "actor": actor, "reason": "journey"}


def validated(conn) -> None:
    ok(conn, "lab_control_transition", move(D2, "draft", "validating"))
    ok(conn, "lab_control_transition", move(D2, "validating", "ready_private"))


def card(revision: str = P2, version: str = CARD2, *, model: str = cc.MODEL,
         serving: str = S2) -> dict:
    return {"rate_card_version": version, "model_id": model, "deployment_revision_id": revision,
            "serving_version_id": serving, "input_rate_per_million": "300",
            "output_rate_per_million": "900", "effective_at": "2026-09-01T00:00:00Z",
            "approved_by": "ops@infrx"}


def publish(revision: str = P2, version: str = CARD2, *, expected: int | None = 1,
            alias: str = ALIAS, **kw) -> dict:
    return {"public_model_id": alias, "card": card(revision, version, **kw),
            "expected_version": expected, "actor": "ops@infrx", "reason": "P-01 approved"}


def rollback(to: int = 1, expected: int = 2) -> dict:
    return {"public_model_id": ALIAS, "to_version": to, "expected_version": expected,
            "actor": "ops@infrx", "reason": "regression"}


def proposal(revision: str = NEW, *, serving: str = S2, provider: str = NEMO,
             state: str = "proposed_public") -> dict:
    return {"deployment_revision_id": revision, "endpoint_id": cc.PROD_ENDPOINT,
            "provider_org_id": provider, "serving_version_id": serving,
            "environment": "prod", "visibility": "public", "state": state,
            "max_input_tokens": 30720, "max_output_tokens": 2048}


def fund(provider: str = OTHER, amount: str = "250",
         op: str = "0f000000-0000-4000-8000-00000000000f") -> dict:
    return {"provider_org_id": provider, "amount": amount, "operation_id": op,
            "actor": "ops@infrx", "reason": "preview budget"}


def events(conn, provider: str = NEMO) -> list[dict]:
    return ok(conn, "lab_control_events", {"provider_org_id": provider})


def listed(conn) -> str:
    """The deployment the alias admits at now (admission's own resolution)."""
    return str(cc.resolve(conn, ALIAS)["deployment_revision_id"])


def state(conn, revision: str) -> str:
    return conn.execute("select state from infrx.deployment_revisions where "
                        "deployment_revision_id = %s", (revision,)).fetchone()[0]


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
def check_transitions_are_a_cas_on_the_providers_own_private_revisions(conn) -> str:
    """LAB-ACCESS / LAB-PUBLISH: the provider moves its own PRIVATE revision only from the
    state it read (a stale expectation is `state_conflict`); another provider's revision and
    any public one are `not_found`; a move 0007 does not allow is `state_conflict`; each move
    is audited with its actor, before and after; an unattributed move is refused."""
    first = ok(conn, "lab_control_transition", move(D2, "draft", "validating"))
    assert (first["state"], first["deployment_revision_id"]) == ("validating", D2), first
    assert refusal(conn, "lab_control_transition", move(D2, "draft", "retired")) == \
        "state_conflict", "a stale expectation moved the revision"
    assert refusal(conn, "lab_control_transition",
                   move(D2, "validating", "ready_private", provider=OTHER)) == "not_found"
    assert refusal(conn, "lab_control_transition", move(D2, "validating", "draft")) == \
        "state_conflict", "a move 0007 forbids"
    got = {s: refusal(conn, "lab_control_transition", move(P1, "active", s))
           for s in ("draining", "retired")}
    got["activate_proposal"] = refusal(conn, "lab_control_transition",
                                       move(P2, "proposed_public", "active"))
    assert got == dict.fromkeys(got, "not_found"), got
    assert state(conn, P1) == "active" and state(conn, P2) == "proposed_public"
    assert refusal(conn, "lab_control_transition",
                   move(D2, "validating", "retired", actor="")) == "invalid_request"
    assert state(conn, D2) == "validating", "an unattributed move landed"
    ok(conn, "lab_control_transition", move(D2, "validating", "ready_private"))
    got = [(e["action"], e["actor"], e["subject"], e["before"], e["after"])
           for e in events(conn)]
    assert got == [
        ("lab_transition", "dev@nemo", D2, {"state": "draft"},
         {"state": "validating", "reason": "journey"}),
        ("lab_transition", "dev@nemo", D2, {"state": "validating"},
         {"state": "ready_private", "reason": "journey"})], got
    assert events(conn, OTHER) == []
    return "CAS on own private revisions, audited; foreign/public not_found; 0007 guards"


@rolled_back
def check_a_proposal_comes_from_a_validated_dev_source(conn) -> str:
    """LAB-PUBLISH: a proposal is a NEW public proposed_public revision of the provider's
    validated (`ready_private`) dev revision's serving version; a draft source, another
    serving version or a private "proposal" is refused; another provider's source is
    `not_found`; the proposal is audited with its source."""
    args = {"proposal": proposal(), "source_revision_id": D2, "actor": "admin@nemo"}
    assert refusal(conn, "lab_control_propose", args) == "state_conflict", "a draft proposed"
    validated(conn)
    assert refusal(conn, "lab_control_propose", {**args, "proposal": proposal(
        provider=OTHER)}) == "not_found"
    assert refusal(conn, "lab_control_propose", {**args, "proposal": proposal(
        serving=S1)}) == "state_conflict", "another serving version proposed"
    assert refusal(conn, "lab_control_propose", {**args, "proposal": proposal(
        state="active")}) == "invalid_request"
    assert refusal(conn, "lab_control_propose", {**args, "source_revision_id": P1}) == \
        "state_conflict", "a public revision as the source"
    row = ok(conn, "lab_control_propose", args)
    assert (row["state"], row["visibility"], row["environment"], row["serving_version_id"]) \
        == ("proposed_public", "public", "prod", S2), row
    # E3L-F2/R205: a repeat call of the same source (even the identical id) is now the
    # open-proposal retry path - see check_a_proposal_retried_after_a_lost_answer_proposes_once
    # for the fresh-id case a real client retry actually sends.
    assert ok(conn, "lab_control_propose", args) == row, "a repeat call opens a second proposal"
    assert [(e["action"], e["subject"], e["after"]) for e in events(conn)][-1] == \
        ("lab_propose", NEW, {"source": D2})
    return "validated dev source only; foreign not_found; audited; a repeat call is idempotent"


@rolled_back
def check_a_proposal_retried_after_a_lost_answer_proposes_once(conn) -> str:
    """E3L-F2/R205: `LabControl.propose` mints a NEW id every call, so a retry of a lost
    answer never reuses the first call's id (unlike "id reused" above, a client-side replay
    the unique_violation catches) - it is a SECOND insert attempt of the SAME source. The
    open proposal answers it; no second `proposed_public` row, no second `lab_propose`
    audit."""
    validated(conn)
    first = ok(conn, "lab_control_propose", {"proposal": proposal(), "source_revision_id": D2,
                                             "actor": "admin@nemo"})
    retried = ok(conn, "lab_control_propose", {"proposal": proposal(RETRY_ID),
                                               "source_revision_id": D2, "actor": "admin@nemo"})
    assert retried["deployment_revision_id"] == first["deployment_revision_id"] == NEW, \
        f"a retry with a fresh id opened a second proposal: {retried}"
    assert conn.execute("select count(*) from infrx.deployment_revisions where "
                        "deployment_revision_id = %s", (RETRY_ID,)).fetchone()[0] == 0
    assert [e["action"] for e in events(conn)].count("lab_propose") == 1
    return "a retry of a lost answer answers the open proposal, never a second insert"


@rolled_back
def check_dev_keys_are_scoped_to_the_providers_dev_endpoint(conn) -> str:
    """LAB-ACCESS: a provider_dev credential is issued only on the provider's own DEV
    endpoint (a prod or another provider's endpoint is `not_found`), filed in the provider's
    own organization (auth.context requires org = provider), stored as a hash only, and
    audited without the hash; a reused hash is refused."""
    args = {"provider_org_id": NEMO, "endpoint_id": cc.DEV_ENDPOINT, "user_id": l2.DEV,
            "key_hash": "hash-l3-dev-1", "prefix": "sk-infrx-l3dev001", "name": "dev"}
    assert refusal(conn, "lab_control_dev_key", {**args, "endpoint_id": cc.PROD_ENDPOINT}) == \
        "not_found"
    assert refusal(conn, "lab_control_dev_key", {**args, "endpoint_id": cc.OTHER_ENDPOINT}) \
        == "not_found"
    assert refusal(conn, "lab_control_dev_key", {**args, "provider_org_id": OTHER}) == \
        "not_found"
    key = ok(conn, "lab_control_dev_key", args)["key_id"]
    row = conn.execute("select org_id::text, audience, provider_org_id::text, "
                       "endpoint_id::text, key_hash, user_id from public.api_keys where id = %s",
                       (key,)).fetchone()
    assert row == (NEMO, "provider_dev", NEMO, cc.DEV_ENDPOINT, "hash-l3-dev-1", None), row
    second = ok(conn, "lab_control_dev_key", {**args, "key_hash": "hash-l3-dev-2"})["key_id"]
    assert second != key
    assert refusal(conn, "lab_control_dev_key", args) == "invalid_request", "hash reused"
    got = [(e["action"], e["actor"], e["subject"], e["after"]) for e in events(conn)]
    assert got[0] == ("lab_dev_key", l2.DEV, key, {"endpoint_id": cc.DEV_ENDPOINT,
                                                   "prefix": "sk-infrx-l3dev001"}), got
    assert "hash-l3" not in str(got)
    return "dev endpoint only, provider's own org, hash stored, audit without it"


@rolled_back
def check_publication_is_a_cas_on_the_listing_version(conn) -> str:
    """LAB-PUBLISH: an operator publishes a proposed public revision at a card, only while the
    alias is still at the version the operator read: the card is inserted, the revision is
    active and the alias's NEXT listing version (audited with before/after); the previous
    revision stays active (admitted pins); a replay at the old version, a card of another
    model or an unknown revision are refused."""
    assert refusal(conn, "lab_control_publish", publish(expected=None)) == "state_conflict"
    assert refusal(conn, "lab_control_publish", publish(expected=2)) == "state_conflict"
    assert refusal(conn, "lab_control_publish", publish(l2.NOBODY)) == "not_found"
    assert refusal(conn, "lab_control_publish", publish(alias="other/model")) == \
        "state_conflict", "another alias's version"
    assert refusal(conn, "lab_control_publish", publish(alias="other/model", expected=None)) \
        == "invalid_request", "listed under another model's alias"
    assert refusal(conn, "lab_control_publish", publish(serving=S1)) == "invalid_request"
    listing = ok(conn, "lab_control_publish", publish())
    assert listing == {"public_model_id": ALIAS, "version": 2, "deployment_revision_id": P2,
                       "rate_card_version": CARD2}, listing
    assert state(conn, P2) == "active" and state(conn, P1) == "active"
    assert listed(conn) == P2
    assert refusal(conn, "lab_control_publish", publish(P3, CARD3)) == "state_conflict", \
        "a publication at a stale version"
    assert refusal(conn, "lab_control_publish", publish(expected=2)) == "state_conflict", \
        "a publication replayed as a second listing"
    last = events(conn)[-1]
    assert (last["action"], last["subject"], last["before"]["version"],
            last["after"]["version"], last["after"]["reason"]) == \
        ("lab_publish", ALIAS, 1, 2, "P-01 approved"), last
    return "CAS on the version; card + active + next listing; previous revision kept"


@rolled_back
def check_dev_revisions_never_reach_app_discovery(conn) -> str:
    """LAB-PUBLISH: a private dev revision - even validated - is never published, never
    listed and never admitted by a consumer alias; only its provider_dev endpoint names it."""
    validated(conn)
    assert refusal(conn, "lab_control_publish", publish(D2)) == "state_conflict"
    for alias in ("nemostation/marlin-2b-dev", "nemostation/marlin-2b@2026-10-01"):
        got = cc.attempt(conn, "select * from infrx.resolve_admission_pins(%s)", (alias,))
        assert got is not None and "not_found" in got, f"{alias} admitted: {got}"
    assert listed(conn) == P1
    return "dev revisions: no publication, no listing, no consumer discovery"


@rolled_back
def check_rollback_is_a_new_listing_and_admitted_jobs_keep_their_pins(conn) -> str:
    """LAB-PUBLISH / ROLLOUT-PIN: a job admitted before a publication keeps its revision and
    card through the alias switch and the rollback; the rollback is a CAS'd new listing
    version repeating an earlier version (its revision and card); the current or a later
    version, version 0, a stale expectation or a drained target are refused."""
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
    ok(conn, "lab_control_publish", publish())
    during, pins2 = admitted()
    assert pins2 == (P2, CARD2), pins2
    assert refusal(conn, "lab_control_rollback", rollback(expected=1)) == "state_conflict"
    assert refusal(conn, "lab_control_rollback", rollback(to=2)) == "not_found"
    assert refusal(conn, "lab_control_rollback", rollback(to=0)) == "not_found"
    conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                 "deployment_revision_id, serving_version_id, input_rate_per_million, "
                 "output_rate_per_million, effective_at, approved_by, provisional) values "
                 "('rc_marlin2b_repriced', %s, %s, %s, 999, 999, '2026-09-02', 'ops', false)",
                 (cc.MODEL, P1, S1))
    listing = ok(conn, "lab_control_rollback", rollback())
    assert listing == {"public_model_id": ALIAS, "version": 3, "deployment_revision_id": P1,
                       "rate_card_version": CARD1}, listing
    assert listed(conn) == P1
    kept = conn.execute("select request_id::text, deployment_revision_id::text, "
                        "rate_card_version from infrx.jobs where request_id in (%s, %s) "
                        "order by admitted_at", (before, during)).fetchall()
    assert kept == [(before, P1, CARD1), (during, P2, CARD2)], kept
    last = events(conn)[-1]
    assert (last["action"], last["before"]["version"], last["after"]["version"]) == \
        ("lab_rollback", 2, 3), last
    conn.execute("update infrx.deployment_revisions set state = 'draining' "
                 "where deployment_revision_id = %s", (P2,))
    assert refusal(conn, "lab_control_rollback", rollback(to=2, expected=3)) == \
        "state_conflict", "rolled back to a draining revision"
    return "rollback = listing v3 repeating v1; jobs keep P1/P2 pins; drained is no target"


@rolled_back
def check_the_dev_wallet_opens_at_zero_and_is_funded_only_by_audited_allocation(conn) -> str:
    """platforms/02-credits: funding opens the provider's provider_dev wallet at 0 and adds
    ONE audited operator allocation (D5's `grant_credit`, `admin_adjust`) plus a lab_fund
    event; the same operation id is the same entry, never a second one; nothing
    nonpositive; no wallet for a provider that does not exist."""
    assert cc.attempt(conn, "select 1 from infrx.credit_wallets where owner_provider_org_id = "
                      "%s and kind = 'provider_dev'", (OTHER,)) is None
    first = ok(conn, "lab_control_fund", fund())
    assert (first["replayed"], first["entry"]["amount"], first["entry"]["kind"]) == \
        (False, "250.00000000", "operator_allocation"), first
    again = ok(conn, "lab_control_fund", fund())
    assert again["replayed"] is True and again["entry"]["entry_id"] == \
        first["entry"]["entry_id"], again
    total = conn.execute("select ledger_total::text from infrx.credit_wallets where "
                         "owner_provider_org_id = %s and kind = 'provider_dev'",
                         (OTHER,)).fetchone()[0]
    assert total == "250.00000000", total
    audit = conn.execute("select actor_principal, action from infrx.audit_entries where "
                         "idempotency_key = 'grant_credit:0f000000-0000-4000-8000-00000000000f'"
                         ).fetchall()
    assert audit == [("ops@infrx", "admin_adjust")], audit
    assert [(e["action"], e["after"]["amount"]) for e in events(conn, OTHER)] == \
        [("lab_fund", "250.00000000")]
    for amount in ("0", "-5"):
        assert refusal(conn, "lab_control_fund", fund(
            amount=amount, op="0f000000-0000-4000-8000-0000000000aa")) == "invalid_request"
    assert refusal(conn, "lab_control_fund", fund(l2.NOBODY)) == "not_found"
    return "opens at 0, one audited allocation per operation, replay-safe"


def _race(conn, fn: str, calls: list[dict]) -> list:
    """Each call from its own connection, released together: its listing version or code."""
    gate, answers = threading.Barrier(len(calls)), [None] * len(calls)

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, fn, calls[i])["version"]
            except psycopg.Error as failed:
                answers[i] = str(failed).splitlines()[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return sorted(map(str, answers))


def _race_propose(conn, calls: list[dict]) -> list[str]:
    """Like `_race`, but for `lab_control_propose`: each thread's own `deployment_revision_id`
    (the id on the winning row every caller is answered with, whether it inserted it or found
    it already open) or its refusal code."""
    gate, answers = threading.Barrier(len(calls)), [None] * len(calls)

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, "lab_control_propose", calls[i])[
                    "deployment_revision_id"]
            except psycopg.Error as failed:
                answers[i] = str(failed).splitlines()[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return answers


def check_a_proposal_retry_race_of_the_same_source_proposes_once(conn) -> str:
    """E3L-F2/R205 under real concurrency (commits its own rows, like the publish race
    above): a crash-and-retry window can genuinely overlap the original request still
    committing, so two propose calls of the SAME source, released together, must still
    agree on ONE open proposal - never both pass the open-proposal check and both insert."""
    conn.execute(f"""
    insert into infrx.serving_versions (serving_version_id, model_version_id, model_id,
      provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version,
      runtime_image_ref, engine_options_digest, precision, capability, created_by)
    select '{S3}', model_version_id, model_id, provider_org_id, '2026-10-03', prompt_harness_ref,
           preprocessor_profile_version, runtime_image_ref, engine_options_digest, precision,
           capability, 'ops' from infrx.serving_versions where serving_version_id = '{S1}';
    insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id,
      provider_org_id, environment, serving_version_id, visibility, state, max_input_tokens,
      max_output_tokens, created_by) values
      ('{D3}', '{cc.DEV_ENDPOINT}', '{NEMO}', 'dev', '{S3}', 'private', 'ready_private',
       30720, 2048, 'dev@nemo');
    """)
    calls = [{"proposal": proposal(RACE_A, serving=S3), "source_revision_id": D3,
             "actor": "admin@nemo"},
             {"proposal": proposal(RACE_B, serving=S3), "source_revision_id": D3,
             "actor": "admin@nemo"}]
    answers = _race_propose(conn, calls)
    assert answers[0] == answers[1] and answers[0] in (RACE_A, RACE_B), \
        f"two racing retries of the same source disagreed: {answers}"
    open_rows = conn.execute(
        "select deployment_revision_id::text from infrx.deployment_revisions where "
        "serving_version_id = %s and state = 'proposed_public'", (S3,)).fetchall()
    assert [r[0] for r in open_rows] == [answers[0]], \
        f"more than one open proposal of the same source: {open_rows}"
    return f"both racing retries answered {answers[0]}; one row, one insert"


def check_two_operators_racing_publish_once(conn) -> str:
    """LAB-PUBLISH races (commit their own rows): two operators approving two proposals of
    one alias from the same version - exactly one publishes, the other is told the alias
    moved on (the CAS, not a colliding insert); two rollbacks from the same version land
    once."""
    approvals = _race(conn, "lab_control_publish", [publish(), publish(P3, CARD3)])
    assert approvals == ["2", f"state_conflict: {ALIAS} is no longer at version 1"], approvals
    rollbacks = _race(conn, "lab_control_rollback", [rollback(), rollback()])
    assert rollbacks == ["3", f"state_conflict: {ALIAS} moved on from version 2"], rollbacks
    return f"approvals {approvals}; rollbacks {rollbacks}"


def check_the_store_composes(conn) -> str:
    """`PgControlStore` is the L3 `ControlStore` over the RPCs: typed rows and refusals."""
    store = PgControlStore(connector(pgharness.dsn(conn.info.dbname)))
    S = DeploymentState
    at = conn.execute("select max(version) from infrx.catalog_listings where "
                      "public_model_id = %s", (ALIAS,)).fetchone()[0]       # after the race
    ready = state(conn, D2) == "ready_private"

    async def go() -> list:
        got = [await store.model_provider(cc.MODEL), await store.model_provider("nope"),
               await store.deployment("nope"), (await store.deployment(D2)).state,
               type(await store.db_now()).__name__]
        dev = await store.endpoint(NEMO, "store-dev", Environment.dev, "dev@nemo")
        got.append(dev == await store.endpoint(NEMO, "store-dev", Environment.dev, "x"))
        try:
            await store.transition(D2, NEMO, expected=S.validating, to=S.ready_private,
                                   actor="dev", reason="x")
        except errors.StateConflict:
            got.append("state_conflict")
        if not ready:
            await store.transition(D2, NEMO, expected=S.draft, to=S.validating, actor="dev",
                                   reason="smoke")
            await store.transition(D2, NEMO, expected=S.validating, to=S.ready_private,
                                   actor="dev", reason="passed")
        got.append((await store.deployment(D2)).state)
        prod = await store.endpoint(NEMO, "store-prod", Environment.prod, "admin")
        proposed = await store.propose(DeploymentRevision(
            deployment_revision_id="c000000b-0000-4000-8000-00000000000b", endpoint_id=prod,
            provider_org_id=NEMO, serving_version_id=S2, environment=Environment.prod,
            visibility=Visibility.public, state=S.proposed_public, max_input_tokens=30720,
            max_output_tokens=2048, created_at=await store.db_now()),
            source_revision_id=D2, actor="admin")
        got.append(proposed.state)
        key = await store.issue_dev_key(provider_org_id=NEMO, endpoint_id=dev, user_id=l2.DEV,
                                        key_hash="hash-store", prefix="sk-infrx-store000",
                                        name="dev")
        got.append(len(key))
        listing = await store.publish(ALIAS, RateCardSnapshot(
            rate_card_version="rc_store", model_id=cc.MODEL,
            deployment_revision_id=proposed.deployment_revision_id, serving_version_id=S2,
            input_rate_per_million="300", output_rate_per_million="900",
            effective_at=await store.db_now(), approved_by="ops@infrx"),
            expected_version=at, actor="ops", reason="approved")
        got.append((listing.version, listing.rate_card_version))
        back = await store.rollback(ALIAS, to_version=1, expected_version=at + 1, actor="ops",
                                    reason="regress")
        got.append((back.version, back.deployment_revision_id == P1))
        entry = await store.fund_dev_wallet(OTHER, Credit("12.5"), operation_id=
                                            "0f000000-0000-4000-8000-0000000000bb",
                                            actor="ops", reason="preview")
        got.append(str(entry.amount))
        got.append([e.action for e in await store.events(NEMO)][-5:])
        return got
    got = asyncio.run(go())
    assert got == [NEMO, None, None, "ready_private" if ready else "draft", "datetime", True, "state_conflict",
                   S.ready_private, S.proposed_public, 36, (at + 1, "rc_store"), (at + 2, True),
                   str(Credit("12.5")),
                   ["lab_transition", "lab_propose", "lab_dev_key", "lab_publish",
                    "lab_rollback"]], got
    return f"store round trip: {got[-1]}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_transitions_are_a_cas_on_the_providers_own_private_revisions,
    check_a_proposal_comes_from_a_validated_dev_source,
    check_a_proposal_retried_after_a_lost_answer_proposes_once,
    check_dev_keys_are_scoped_to_the_providers_dev_endpoint,
    check_publication_is_a_cas_on_the_listing_version,
    check_dev_revisions_never_reach_app_discovery,
    check_rollback_is_a_new_listing_and_admitted_jobs_keep_their_pins,
    check_the_dev_wallet_opens_at_zero_and_is_funded_only_by_audited_allocation,
    check_two_operators_racing_publish_once, check_a_proposal_retry_race_of_the_same_source_proposes_once,
    check_the_store_composes)}


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
