#!/usr/bin/env python3
"""D7 (wave-5 LW1, lab-sql; 13-lab-improvement-handoffs §D7; F3 R157-R161): the Lab catalog
and evaluation coordination on real PostgreSQL - DATA-IMMUTABLE, DATA-RIGHTS, EVAL-DURABLE and
DUR-RLS for `0029_lab_data.sql`, composed with `PgLabDataStore` and D2's `OutboxRelay`.

World: test_l2sql_access's role matrix (two consumers, NEMO and OTHER providers, one user in
both products) plus one current grant to each provider and one registered source under each.
Each `check_*` is the check a mutant in `code_mutants_d7.py` must break; the rolled-back ones
leave nothing behind, the race and kill drills commit their own objects.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d7_lab_data.py
"""
from __future__ import annotations

import asyncio
import json
import threading

import psycopg
import pytest
from infrx.contracts.lab import records, states
from infrx.contracts.v2 import records as v2
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore, grant_ref
from infrx.state.outbox import OutboxRelay

from . import checks_credit as cc
from . import pgharness
from . import test_l2sql_access as l2

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d7"

NEMO, OTHER, C1, BOTH = l2.NEMO, l2.OTHER, l2.C1, l2.BOTH
call, ok, refusal, rolled_back = l2.call, l2.ok, l2.refusal, l2.rolled_back
TABLES = ("lab_sources", "lab_records", "lab_dataset_samples", "lab_eval_runs",
          "lab_eval_cases", "lab_eval_attempts", "lab_eval_results", "lab_checkpoint_receipts",
          "lab_outbox")
RPCS = ("lab_register_source", "lab_publish", "lab_resolve", "lab_accessible_samples",
        "lab_create_run", "lab_lease_case", "lab_heartbeat", "lab_finish_attempt",
        "lab_recover", "lab_cancel_run", "lab_run_status", "lab_receive_checkpoint",
        "lab_checkpoint_transition", "lab_outbox_pending", "lab_outbox_ack",
        "lab_outbox_release", "lab_outbox_error")
EVALUATOR = f"lab:evaluator:{NEMO}:00000032-0000-4000-8000-000000000032@sha256:{'a' * 64}"
SERVING = f"lab:serving:{NEMO}:00000028-0000-4000-8000-000000000028@sha256:{'f' * 64}"
PAYER = f"lab:payer:{NEMO}:0000003c-0000-4000-8000-00000000003c@sha256:{'a' * 64}"
W: dict[str, str] = {}          # the seeded world: grant and source refs


def uid(n: int, tag: int = 0xd7) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012x}"


def body(payload: dict) -> str:
    return records.canonical(payload).decode()


def publish(conn, payload: dict, provider: str = NEMO) -> str:
    return ok(conn, "lab_publish", {"provider_org_id": provider, "actor": "dev@nemo",
                                    "body": body(payload)})["ref"]


def refused_publish(conn, payload: dict, provider: str = NEMO) -> str | None:
    return refusal(conn, "lab_publish", {"provider_org_id": provider, "actor": "dev@nemo",
                                         "body": body(payload)})


def manifest(dataset: str, *, n: int = 3, version: int = 1, provider: str = NEMO,
             source: str | None = None, grant: str | None = None, parents=(), tag: int = 0xd7,
             salt: str = "") -> dict:
    ids = [uid(i, tag) for i in range(1, n + 1)]
    return {"schema": "lab.dataset_manifest.1", "provider_org_id": provider,
            "dataset_id": dataset, "version": version, "created_at": "2026-09-27T10:00:00Z",
            "derivation": "derive" if parents else "import", "parent_refs": list(parents),
            "samples": [{"sample_id": s, "modality": "text",
                         "source_ref": source or W["source"], "grant_ref": grant or W["grant"],
                         "content_digest": f"sha256:{i:064x}", "group_key": f"g{i}{salt}"}
                        for i, s in enumerate(ids, 1)],
            "splits": {"train": ids[:-1], "validation": [], "holdout": ids[-1:]}}


def harness(harness_id: str = uid(20, 0xa0)) -> dict:
    return {"schema": "lab.harness_revision.1", "provider_org_id": NEMO,
            "harness_id": harness_id, "version": 1, "created_at": "2026-09-27T10:00:00Z",
            "adapter": "text", "prompt_template": "{{input}}", "processor_profile": "p1",
            "input_mapping": {"input": "sample.text"}, "tools": []}


def eval_run(run_id: str, dataset_ref: str, harness_ref: str, max_cases: int = 100) -> dict:
    return {"schema": "lab.eval_run.1", "provider_org_id": NEMO, "run_id": run_id,
            "created_at": "2026-09-27T10:00:00Z", "dataset_ref": dataset_ref,
            "harness_ref": harness_ref, "serving_ref": SERVING, "evaluator_ref": EVALUATOR,
            "seed": 7, "environment": "dev", "max_cases": max_cases, "state": "queued",
            "idempotency_key": records.run_key(run_id), "budgets": []}


def external_run(external_run_id: str, dataset_ref: str) -> dict:
    return {"schema": "lab.external_run.1", "provider_org_id": NEMO,
            "external_run_id": external_run_id, "purpose": "external_judging",
            "connector": "manual-bundle", "dataset_ref": dataset_ref,
            "submit_key": records.submit_key(external_run_id), "state": "prepared",
            "budget": {"limit": {"unit": "PROVIDER_USD", "value": "40.00000000"},
                       "reserved": {"unit": "PROVIDER_USD", "value": "0.00000000"},
                       "payer_ref": PAYER}}


def a_run(conn, n: int = 3, tag: int = 0xe1, max_cases: int = 100) -> tuple[str, dict]:
    """A published dataset of `n` samples, a harness and a created run over it."""
    dataset = publish(conn, manifest(uid(1, tag), n=n, tag=tag))
    run_id = uid(2, tag)
    run_ref = publish(conn, eval_run(run_id, dataset, publish(conn, harness(uid(3, tag))),
                                     max_cases))
    return dataset, ok(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": run_ref})


def lease(conn, run_id: str, worker: str = "w1", lease_s: float = 30) -> dict | None:
    return ok(conn, "lab_lease_case", {"provider_org_id": NEMO, "run_id": run_id,
                                       "worker_id": worker, "lease_s": lease_s})


def finish(lease_: dict, outcome: str = "succeeded", score: int = 1, cost: str = "0.25000000"):
    results = [{"evaluator_ref": EVALUATOR, "body": json.dumps({"score": score})}] \
        if outcome == "succeeded" else []
    return {"lease": lease_, "outcome": outcome, "results": results,
            "cost": {"unit": "CREDIT", "value": cost}}


def advance(conn, seconds: int) -> None:
    conn.execute("update infrx_test.clock set offset_s = offset_s + make_interval(secs => %s)",
                 (seconds,))


def count(conn, sql: str, *params) -> int:
    return conn.execute(sql, params).fetchone()[0]


def seed(conn) -> None:
    """test_l2sql_access's world; C1 grants NEMO provider_sharing + training, BOTH grants
    OTHER provider_sharing; one source registered under each."""
    l2.seed(conn)
    nemo = call(conn, "lab_put_access_grant", l2.scope(
        conn, purposes=["provider_sharing", "training"]))
    other = call(conn, "lab_put_access_grant", l2.scope(
        conn, owner=BOTH, provider=OTHER, model_ids=[l2.OTHER_MODEL],
        purposes=["provider_sharing"]))
    W["grant"] = grant_ref(v2.AccessGrant.model_validate(nemo))
    W["other_grant"] = grant_ref(v2.AccessGrant.model_validate(other))
    W["grant_id"] = nemo["grant_id"]
    W["source"] = call(conn, "lab_register_source", {
        "provider_org_id": NEMO, "source_id": uid(1, 0x5c), "actor": "dev@nemo",
        "content_digest": f"sha256:{'1' * 64}", "grant_ref": W["grant"]})["ref"]
    W["other_source"] = call(conn, "lab_register_source", {
        "provider_org_id": OTHER, "source_id": uid(2, 0x5c), "actor": "dev@other",
        "content_digest": f"sha256:{'2' * 64}", "grant_ref": W["other_grant"]})["ref"]


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads or writes a Lab table or executes a Lab RPC; the
    platform role reads the tables and writes them only through the RPCs."""
    probes = [f"select count(*) from infrx.{t}" for t in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached Lab state: {reached}"
    unread = [t for t in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(t)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [t for t in TABLES if not (cc.refused_as(
        conn, "service", f"delete from infrx.{t}") or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    unguarded = [t for t in TABLES if not conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"infrx.{t}",)
    ).fetchone()[0]]
    assert not unguarded, f"row security is off on {unguarded}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; service reads"


@rolled_back
def check_publication_is_content_addressed_and_immutable(conn) -> str:
    """DATA-IMMUTABLE: the stored ref is the contract's `ref_of` (the digest re-derived from
    the stored bytes); the same bytes again are the same ref and no second row; other bytes
    for the same version are `state_conflict`; a derived version needs a published parent;
    membership and splits are the manifest's; nothing published is edited or removed."""
    payload = manifest(uid(1, 0xa1))
    ref = publish(conn, payload)
    assert ref == records.ref_of(payload), (ref, records.ref_of(payload))
    assert publish(conn, payload) == ref, "the same bytes are another ref"
    assert count(conn, "select count(*) from infrx.lab_records where object_id = %s",
                 payload["dataset_id"]) == 1
    changed = {**payload, "created_at": "2026-09-28T10:00:00Z"}
    assert refused_publish(conn, changed) == "state_conflict", "a version was redefined"
    rows = conn.execute("select sample_id::text, split, source_id::text, grant_id::text "
                        "from infrx.lab_dataset_samples where dataset_ref = %s "
                        "order by sample_id", (ref,)).fetchall()
    ids = [s["sample_id"] for s in payload["samples"]]
    assert rows == [(i, "holdout" if i == ids[-1] else "train", uid(1, 0x5c), W["grant_id"])
                    for i in ids], rows
    derived = manifest(uid(1, 0xa1), version=2, parents=[ref])
    assert publish(conn, derived) == records.ref_of(derived)
    orphan = manifest(uid(4, 0xa1), parents=[ref.replace(ref[-64:], "0" * 64)])
    assert refused_publish(conn, orphan) == "not_found", "a parent that was never published"
    forged = cc.attempt(conn, "insert into infrx.lab_records (ref, kind, provider_org_id, "
                        "object_id, schema_id, body, published_by) values (%s, 'harness', %s, "
                        "%s, 'lab.harness_revision.1', '{}', 'x')",
                        (f"lab:harness:{NEMO}:{uid(5, 0xa1)}@sha256:{'0' * 64}", NEMO,
                         uid(5, 0xa1)))
    assert forged is not None and "lab_records_content_addressed" in forged, forged
    for table in ("lab_records", "lab_dataset_samples", "lab_sources"):
        for sql in (f"update infrx.{table} set provider_org_id = provider_org_id"
                    if table != "lab_dataset_samples" else
                    f"update infrx.{table} set split = split",
                    f"delete from infrx.{table}", f"truncate infrx.{table} cascade"):
            got = cc.attempt(conn, sql)
            assert got is not None and got.startswith("23514"), f"`{sql}` was allowed: {got}"
    return "ref = ref_of; replay one row; version redefinition 409; lineage; rows immutable"


@rolled_back
def check_refs_resolve_only_for_their_provider(conn) -> str:
    """DATA-RIGHTS foreign IDs: a record resolves for its provider only (another provider and
    an unknown digest are `not_found`, as `FakeLabCatalog`); a publisher cannot publish for
    another provider, cite another provider's source or grant, cite a mutable ref, bind a
    sample to a grant its source was not captured under, or publish an unreferable schema;
    a source registers only under the provider's own grant."""
    ref = publish(conn, manifest(uid(1, 0xa2)))
    got = ok(conn, "lab_resolve", {"provider_org_id": NEMO, "ref": ref})
    assert got["ref"] == ref and json.loads(got["body"])["dataset_id"] == uid(1, 0xa2)
    unknown = ref[:-64] + "0" * 64
    for provider, r in ((OTHER, ref), (NEMO, unknown)):
        assert refusal(conn, "lab_resolve", {"provider_org_id": provider, "ref": r}) == \
            "not_found", f"{provider} resolved {r}"
    cases = {
        "forbidden": [(manifest(uid(2, 0xa2)), OTHER)],
        "not_found": [(manifest(uid(3, 0xa2), source=W["other_source"]), NEMO),
                      (manifest(uid(4, 0xa2), grant=W["other_grant"]), NEMO),
                      (manifest(uid(5, 0xa2), grant=W["grant"].replace(NEMO, OTHER)), NEMO)],
        "invalid_request": [
            (manifest(uid(6, 0xa2), source=W["source"].split("@")[0] + "@latest"), NEMO),
            ({**harness(), "schema": "lab.attempt.1"}, NEMO),
            ({**harness(uid(10, 0xa2)), "version": 0}, NEMO)],
    }
    cases["not_found"].append(({**eval_run(uid(9, 0xa2), ref, publish(conn, harness(uid(11, 0xa2)))),
                                "serving_ref": SERVING.replace(NEMO, OTHER)}, NEMO))
    seen = 0
    for code, attempts in cases.items():
        for payload, provider in attempts:
            got = refused_publish(conn, payload, provider)
            assert got == code, f"{code} expected, got {got}"
            seen += 1
    # a second grant to NEMO, and a sample citing it with a source captured under the first
    second = ok(conn, "lab_put_access_grant", l2.scope(conn, owner=BOTH,
                                                        purposes=["provider_sharing"]))
    rebound = manifest(uid(7, 0xa2), grant=grant_ref(v2.AccessGrant.model_validate(second)))
    assert refused_publish(conn, rebound) == "invalid_request", "a sample's grant is not its " \
        "source's"
    source = {"provider_org_id": NEMO, "source_id": uid(1, 0x5c), "actor": "dev@nemo",
              "content_digest": f"sha256:{'1' * 64}", "grant_ref": W["grant"]}
    assert ok(conn, "lab_register_source", source)["ref"] == W["source"], "not a replay"
    for over, code in (({"content_digest": f"sha256:{'3' * 64}"}, "state_conflict"),
                       ({"source_id": uid(8, 0xa2), "grant_ref": W["other_grant"]}, "not_found"),
                       ({"source_id": uid(8, 0xa2), "content_digest": "md5:1"},
                        "invalid_request")):
        got = refusal(conn, "lab_register_source", {**source, **over})
        assert got == code, f"source {over}: {got}, expected {code}"
    return f"resolve own only; {seen + 4} foreign, mutable or unbound writes refused"


@rolled_back
def check_a_stale_grant_stops_access_and_scheduling(conn) -> str:
    """DATA-RIGHTS: access and scheduling read the grant's CURRENT version for the purpose -
    a purpose the grant lacks reads nothing; after revocation (or past expiry) no sample is
    accessible and a run cannot be created; the manifest still resolves (audit evidence
    only) and a new source cannot be registered under the revoked grant."""
    dataset = publish(conn, manifest(uid(1, 0xa3), n=4))

    def accessible(purpose: str) -> int:
        return len(ok(conn, "lab_accessible_samples", {"provider_org_id": NEMO,
                                                       "dataset_ref": dataset,
                                                       "purpose": purpose}))
    assert (accessible("provider_sharing"), accessible("training"), accessible("capture")) \
        == (4, 4, 0)
    assert ok(conn, "lab_accessible_samples", {"provider_org_id": OTHER, "dataset_ref": dataset,
                                               "purpose": "provider_sharing"}) == []
    run_ref = publish(conn, eval_run(uid(2, 0xa3), dataset, publish(conn, harness(uid(3, 0xa3)))))
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": l2.org(conn, C1),
                                         "recipient_provider_org_id": NEMO})
    assert accessible("provider_sharing") == 0, "a revoked grant still gives access"
    assert refusal(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": run_ref}) == \
        "forbidden", "a run was scheduled over revoked content"
    assert ok(conn, "lab_resolve", {"provider_org_id": NEMO, "ref": dataset})["ref"] == dataset
    assert refusal(conn, "lab_register_source", {
        "provider_org_id": NEMO, "source_id": uid(4, 0xa3), "actor": "dev@nemo",
        "content_digest": f"sha256:{'4' * 64}", "grant_ref": W["grant"]}) == "forbidden"
    regrant = ok(conn, "lab_put_access_grant", l2.scope(
        conn, purposes=["provider_sharing"], expires_at="2099-01-01T00:00:00Z"))
    assert accessible("provider_sharing") == 4 and regrant["grant_id"] == W["grant_id"]
    conn.execute("update infrx_test.clock set offset_s = offset_s + "
                 "('2099-01-02'::timestamptz - infrx.now())")
    assert accessible("provider_sharing") == 0, "an expired grant still gives access"
    return "purpose-scoped; revoked and expired grants give no access and no run"


@rolled_back
def check_leases_are_fenced_and_one_result_per_case(conn) -> str:
    """EVAL-DURABLE: a run is one case per sample (up to max_cases) and one `eval_run` event,
    and creating it again is the same run; a lease is one attempt; a foreign worker, an
    expired lease and a recovered attempt are `stale_lease`; recovery puts the case back and
    the next lease is attempt 2; a finish records one result per evaluator, the case and
    the cost, replays to the same answer and refuses another outcome under the same lease."""
    _, run = a_run(conn, n=3, max_cases=2)
    run_id = run["run_id"]
    assert run["cases"] == {"pending": 2} and run["state"] == "queued", run
    again = ok(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": run["run_ref"]})
    assert again["run_id"] == run_id and again["cases"] == {"pending": 2}
    assert count(conn, "select count(*) from infrx.lab_outbox where kind = 'eval_run' and "
                 "payload->>'run_id' = %s", run_id) == 1, "a replay queued a second event"
    assert refusal(conn, "lab_lease_case", {"provider_org_id": OTHER, "run_id": run_id,
                                            "worker_id": "w9", "lease_s": 30}) == "not_found"
    first = lease(conn, run_id, "w1", 30)
    assert (first["attempt"], first["idempotency_key"]) == \
        (1, records.attempt_key(run_id, first["case_id"], 1)), first
    other = lease(conn, run_id, "w2", 30)
    assert other["case_id"] != first["case_id"] and lease(conn, run_id, "w3") is None
    forged = {**first, "worker_id": "w2"}
    assert refusal(conn, "lab_heartbeat", {"lease": forged, "lease_s": 30}) == "stale_lease"
    assert refusal(conn, "lab_finish_attempt", finish(forged)) == "stale_lease"
    assert refusal(conn, "lab_heartbeat", {"lease": {**first, "provider_org_id": OTHER},
                                           "lease_s": 30}) == "not_found"
    ok(conn, "lab_heartbeat", {"lease": other, "lease_s": 120})
    advance(conn, 60)
    assert refusal(conn, "lab_heartbeat", {"lease": first, "lease_s": 30}) == "stale_lease", \
        "an expired lease was renewed"
    assert ok(conn, "lab_recover", {}) == {"expired": 1}, "recovery missed the expired lease"
    assert refusal(conn, "lab_finish_attempt", finish(first)) == "stale_lease"
    retry = lease(conn, run_id, "w3", 30)
    assert retry is not None and (retry["case_id"], retry["attempt"]) == \
        (first["case_id"], 2), f"the recovered case was not offered again: {retry}"
    twice = finish(retry)
    twice["results"] = twice["results"] * 2
    foreign = finish(retry)
    foreign["results"] = [{**foreign["results"][0],
                           "evaluator_ref": EVALUATOR.replace(NEMO, OTHER)}]
    assert (refusal(conn, "lab_finish_attempt", twice),
            refusal(conn, "lab_finish_attempt", foreign),
            refusal(conn, "lab_finish_attempt", {**finish(other), "outcome": "failed"})) == \
        ("invalid_request", "not_found", "invalid_request"), "a result was misrecorded"
    done = ok(conn, "lab_finish_attempt", finish(retry))
    assert done["state"] == "succeeded", done
    assert ok(conn, "lab_finish_attempt", finish(retry)) == done, "a replay is not the answer"
    assert refusal(conn, "lab_heartbeat", {"lease": retry, "lease_s": 30}) == "stale_lease", \
        "a finished attempt was renewed"
    assert refusal(conn, "lab_finish_attempt", finish(retry, score=0)) == \
        "idempotency_conflict", "another outcome replaced a finished attempt"
    assert count(conn, "select count(*) from infrx.lab_eval_results where run_id = %s",
                 run_id) == 1
    got = cc.attempt(conn, "update infrx.lab_eval_results set body = '{}'")
    assert got is not None and got.startswith("23514"), f"a result was rewritten: {got}"
    status = ok(conn, "lab_run_status", {"provider_org_id": NEMO, "run_id": run_id})
    assert status["cases"] == {"done": 1, "leased": 1}, status
    assert status["attempts"] == {"expired": 1, "succeeded": 1, "leased": 1}, status
    assert status["costs"] == {"CREDIT": "0.25000000"}, status
    assert refusal(conn, "lab_run_status", {"provider_org_id": OTHER, "run_id": run_id}) == \
        "not_found"
    return f"2 cases; attempts {status['attempts']}; one result; replay stable"


@rolled_back
def check_run_and_checkpoint_states_follow_the_contract(conn) -> str:
    """R161: the persisted machines are exactly the contract's (run, case, attempt,
    checkpoint); a run whose cases are all terminal has succeeded and cannot be cancelled;
    a cancelled run leases nothing and fences its live leases; an undeclared checkpoint
    move is `state_conflict`."""
    kinds = ("run", "case", "attempt", "checkpoint")
    everything = {(k, a, b) for k in kinds for a in states.STATES[k] for b in states.STATES[k]}
    declared = {(k, a, b) for k in kinds for a, bs in states.TRANSITIONS[k].items() for b in bs}
    allowed = {t for t in everything if conn.execute(
        "select infrx.lab_may_transition(%s, %s, %s)", t).fetchone()[0]}
    assert allowed == declared, (allowed ^ declared)
    _, run = a_run(conn, n=1, tag=0xe2)
    ok(conn, "lab_finish_attempt", finish(lease(conn, run["run_id"])))
    status = ok(conn, "lab_run_status", {"provider_org_id": NEMO, "run_id": run["run_id"]})
    assert status["state"] == "succeeded", status
    assert refusal(conn, "lab_cancel_run", {"provider_org_id": NEMO,
                                            "run_id": run["run_id"]}) == "state_conflict"
    _, live = a_run(conn, n=2, tag=0xe3)
    held = lease(conn, live["run_id"])
    assert ok(conn, "lab_cancel_run", {"provider_org_id": NEMO,
                                       "run_id": live["run_id"]})["state"] == "cancelled"
    assert refusal(conn, "lab_lease_case", {"provider_org_id": NEMO, "run_id": live["run_id"],
                                            "worker_id": "w", "lease_s": 30}) == \
        "already_terminal"
    assert refusal(conn, "lab_finish_attempt", finish(held)) == "stale_lease"
    got = cc.attempt(conn, "update infrx.lab_eval_runs set state = 'running' where run_id = %s",
                     (live["run_id"],))
    assert got is not None and "state_conflict" in got, f"a cancelled run resumed: {got}"
    return f"{len(declared)} declared moves, no other; terminal runs stay terminal"


@rolled_back
def check_checkpoint_delivery_is_received_once(conn) -> str:
    """EVAL-DURABLE duplicate callback: a receipt and ONE `checkpoint_received` event commit
    together; the same delivery again is the same receipt and no event; another artifact
    under the id is `idempotency_conflict`; a foreign external run is `not_found`; moves
    follow the checkpoint machine, provider-scoped."""
    dataset = publish(conn, manifest(uid(1, 0xa5)))
    ext = publish(conn, external_run(uid(2, 0xa5), dataset))
    delivery = {"provider_org_id": NEMO, "checkpoint_id": uid(3, 0xa5),
                "external_run_ref": ext, "artifact_digest": f"sha256:{'7' * 64}"}
    first = ok(conn, "lab_receive_checkpoint", delivery)
    assert first["state"] == "received", first
    assert ok(conn, "lab_receive_checkpoint", delivery) == first, "a redelivery changed it"
    assert count(conn, "select count(*) from infrx.lab_outbox where kind = "
                 "'checkpoint_received' and payload->>'checkpoint_id' = %s",
                 uid(3, 0xa5)) == 1, "a redelivery queued a second event"
    assert refusal(conn, "lab_receive_checkpoint", {
        **delivery, "artifact_digest": f"sha256:{'8' * 64}"}) == "idempotency_conflict"
    assert refusal(conn, "lab_receive_checkpoint", {
        **delivery, "checkpoint_id": uid(5, 0xa5), "artifact_digest": "md5:1"}) == \
        "invalid_request"
    assert refusal(conn, "lab_receive_checkpoint", {**delivery, "provider_org_id": OTHER,
                                                    "checkpoint_id": uid(4, 0xa5)}) == \
        "not_found", "a checkpoint was received for another provider's run"
    move = {"provider_org_id": NEMO, "checkpoint_id": uid(3, 0xa5)}
    assert refusal(conn, "lab_checkpoint_transition", {**move, "provider_org_id": OTHER,
                                                       "state": "validated"}) == "not_found"
    assert refusal(conn, "lab_checkpoint_transition", {**move, "state": "evaluated"}) == \
        "state_conflict"
    for state in ("validated", "evaluated"):
        assert ok(conn, "lab_checkpoint_transition", {**move, "state": state})["state"] == state
    assert refusal(conn, "lab_checkpoint_transition", {**move, "state": "rejected"}) == \
        "state_conflict", "an evaluated checkpoint was rejected"
    return "one receipt, one event; conflicting artifact 409; foreign 404; machine enforced"


def check_the_relay_redelivers_a_lost_acknowledgment(conn) -> str:
    """EVAL-DURABLE kill around ack, through D2's `OutboxRelay` over `PgLabDataStore`: a
    relay that claimed events and died never acknowledged them; another relay gets them only
    after the redelivery window, indexes each once and acknowledges; the dead relay's late
    acknowledgment is refused (commits its own run)."""
    _, run = a_run(conn, n=1, tag=0xb1)
    ok(conn, "lab_receive_checkpoint", {
        "provider_org_id": NEMO, "checkpoint_id": uid(9, 0xb1),
        "external_run_ref": publish(conn, external_run(uid(8, 0xb1), run["dataset_ref"])),
        "artifact_digest": f"sha256:{'9' * 64}"})
    store = PgLabDataStore(connector(pgharness.dsn(conn.info.dbname)))
    mine = {run["run_id"], uid(9, 0xb1)}

    class Index:
        """Indexes each event; the dead relay's late acknowledgment lands mid-pump, after
        the live relay claimed the events and before it acknowledged them."""

        def __init__(self, dead) -> None:
            self.seen: list[str] = []
            self.dead, self.stale_ack = dead, None

        async def enqueue(self, event) -> bool:
            if self.stale_ack is None:
                self.stale_ack = await store.acknowledge_dispatch(
                    [e.event_id for e in self.dead], worker_id="dead")
            self.seen.append(event.payload.get("run_id") or event.payload["checkpoint_id"])
            return True

    async def drill():
        dead = await store.dispatch_pending(limit=100, worker_id="dead", redelivery_s=30)
        index = Index(dead)
        relay = OutboxRelay(store=store, scheduler=index, worker_id="live", redelivery_s=30)
        early = await relay.pump()
        conn.execute("update infrx_test.clock set offset_s = offset_s + interval '31 s'")
        try:
            late = await relay.pump()
            conn.execute("update infrx_test.clock set offset_s = offset_s + interval '31 s'")
            after = await relay.pump()
        finally:
            conn.execute("update infrx_test.clock set offset_s = offset_s - interval '62 s'")
        return dead, early, late, after, index.seen, index.stale_ack
    dead, early, late, after, seen, stale_ack = asyncio.run(drill())
    assert mine <= {e.payload.get("run_id") or e.payload.get("checkpoint_id") for e in dead}
    assert early["read"] == 0, f"a claimed event was redelivered early: {early}"
    assert mine <= set(seen) and len(seen) == len(set(seen)), seen
    assert late["acknowledged"] == late["indexed"] == len(seen), late
    assert stale_ack == 0, "the dead relay's late acknowledgment landed"
    assert after["read"] == 0, f"an acknowledged event was delivered again: {after}"
    return f"claimed {len(dead)}; redelivered {len(seen)} once after the window; late ack refused"


def check_two_publishers_race_to_one_version(conn) -> str:
    """DATA-IMMUTABLE race: two publishers of the same dataset version at once - the second
    waits for the first; with other bytes it is `state_conflict`, with the same bytes it is
    the same ref; one row either way (commits its own dataset)."""
    first_payload = manifest(uid(1, 0xb2), salt="a")
    other_payload = manifest(uid(1, 0xb2), salt="b")
    outcomes = {}
    for label, second_payload in (("other bytes", other_payload),
                                  ("same bytes", first_payload)):
        version = 1 if label == "other bytes" else 2
        a = {**first_payload, "version": version}
        b = {**second_payload, "version": version}
        one = psycopg.connect(pgharness.dsn(conn.info.dbname))
        two = psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True)
        answers: dict = {}
        try:
            won = call(one, "lab_publish", {"provider_org_id": NEMO, "actor": "a",
                                            "body": body(a)})["ref"]

            def second() -> None:
                try:
                    answers["ref"] = call(two, "lab_publish", {"provider_org_id": NEMO,
                                                               "actor": "b", "body": body(b)})
                except psycopg.Error as failed:
                    answers["error"] = str(failed).split(":")[0]
            racer = threading.Thread(target=second)
            racer.start()
            racer.join(1.0)
            assert racer.is_alive(), f"{label}: the second publisher did not wait"
            one.commit()
            racer.join(10.0)
        finally:
            one.close()
        two.close()
        outcomes[label] = answers
        rows = count(conn, "select count(*) from infrx.lab_records where kind = 'dataset' and "
                     "object_id = %s and version = %s", uid(1, 0xb2), version)
        assert rows == 1, f"{label}: {rows} rows for one version"
        if label == "other bytes":
            assert answers == {"error": "state_conflict"}, answers
        else:
            assert answers.get("ref", {}).get("ref") == won, answers
    return f"racing publishers: {outcomes}"


def check_a_kill_around_commit_recovers_once(conn) -> str:
    """DATA-IMMUTABLE/EVAL-DURABLE kill drills: a publication and a finish killed before
    commit leave nothing (the partial upload is invisible, the lease still live); done again
    they commit once; a kill after commit (the answer lost) replays to the same ref and the
    same attempt (commits its own objects)."""
    payload = manifest(uid(1, 0xb3), n=2, tag=0xb3)
    ref = records.ref_of(payload)

    def killed(work, unseen: str) -> None:
        victim = psycopg.connect(pgharness.dsn(conn.info.dbname))
        try:
            work(victim)
            assert count(conn, unseen) == 0, f"uncommitted work is visible: {unseen}"
            conn.execute("select pg_terminate_backend(%s)", (victim.info.backend_pid,))
            with pytest.raises(psycopg.Error):
                victim.commit()
        finally:
            victim.close()
    killed(lambda c: call(c, "lab_publish", {"provider_org_id": NEMO, "actor": "k",
                                             "body": body(payload)}),
           f"select count(*) from infrx.lab_records where ref = '{ref}'")
    assert count(conn, "select count(*) from infrx.lab_records where ref = %s", ref) == 0
    assert count(conn, "select count(*) from infrx.lab_dataset_samples where dataset_ref = %s",
                 ref) == 0, "a killed publication left samples behind"
    assert publish(conn, payload) == ref and publish(conn, payload) == ref
    run_ref = publish(conn, eval_run(uid(2, 0xb3), ref, publish(conn, harness(uid(3, 0xb3)))))
    run = ok(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": run_ref})
    held = lease(conn, run["run_id"])
    killed(lambda c: call(c, "lab_finish_attempt", finish(held)),
           f"select count(*) from infrx.lab_eval_results where run_id = '{run['run_id']}'")
    assert count(conn, "select count(*) from infrx.lab_eval_attempts where run_id = %s and "
                 "state = 'leased'", run["run_id"]) == 1, "a killed finish changed the lease"
    first = ok(conn, "lab_finish_attempt", finish(held))
    assert ok(conn, "lab_finish_attempt", finish(held)) == first
    assert count(conn, "select count(*) from infrx.lab_eval_results where run_id = %s",
                 run["run_id"]) == 1
    return "killed publication invisible; killed finish left the lease; replays once"


@rolled_back
def check_large_fixture_queries_use_their_indexes(conn) -> str:
    """D7.c: with 20 000 samples, cases, attempts and events, the hot reads of the RPCs -
    the next pending case of a run, the expired leases, the pending outbox, a dataset's
    samples - plan on their indexes, never a scan of the whole relation."""
    dataset = publish(conn, manifest(uid(1, 0xb4), n=2, tag=0xb4))
    big = publish(conn, manifest(uid(2, 0xb4), n=1, tag=0xb5))
    run_ref = publish(conn, eval_run(uid(3, 0xb4), big, publish(conn, harness(uid(4, 0xb4)))))
    run_id = ok(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": run_ref})["run_id"]
    # fixture rows straight into the relations (inserts only: no guard fires)
    conn.execute("insert into infrx.lab_dataset_samples (dataset_ref, sample_id, split, "
                 "modality, source_id, grant_id, grant_version, content_digest, group_key) "
                 "select %s, gen_random_uuid(), 'train', 'text', source_id, grant_id, "
                 "grant_version, 'sha256:' || repeat('0', 64), 'g' || i "
                 "from infrx.lab_sources, generate_series(1, 20000) i where source_id = %s",
                 (big, uid(1, 0x5c)))
    conn.execute("insert into infrx.lab_eval_cases (run_id, case_id, state, attempts) "
                 "select %s, gen_random_uuid(), 'done', 1 from generate_series(1, 20000)",
                 (run_id,))
    conn.execute("insert into infrx.lab_eval_attempts (run_id, case_id, attempt, worker_id, "
                 "state, acquired_at, expires_at, finished_at) select run_id, case_id, 1, 'w', "
                 "'succeeded', infrx.now(), infrx.now(), infrx.now() "
                 "from infrx.lab_eval_cases where run_id = %s and state = 'done'", (run_id,))
    conn.execute("insert into infrx.lab_outbox (provider_org_id, kind, payload, "
                 "acknowledged_at) select %s, 'eval_run', '{}', infrx.now() "
                 "from generate_series(1, 20000)", (NEMO,))
    for table in TABLES:
        conn.execute(f"analyze infrx.{table}")
    plans = {
        "lab_eval_cases_pending": ("select case_id from infrx.lab_eval_cases where run_id = %s "
                                   "and state = 'pending' order by case_id limit 1", (run_id,)),
        "lab_eval_attempts_expiry": ("select 1 from infrx.lab_eval_attempts where state = "
                                     "'leased' and expires_at <= infrx.now()", None),
        "lab_outbox_pending": ("select event_id from infrx.lab_outbox where acknowledged_at is "
                               "null and available_at <= infrx.now() order by available_at "
                               "limit 100", None),
        "lab_dataset_samples_pkey": ("select sample_id from infrx.lab_dataset_samples where "
                                     "dataset_ref = %s", (dataset,)),
    }
    wrong = {}
    for index, (sql, params) in plans.items():
        plan = json.dumps(conn.execute(f"explain (format json) {sql}", params).fetchone()[0])
        if f'"Index Name": "{index}"' not in plan or '"Seq Scan"' in plan:
            wrong[index] = plan[:300]
    assert not wrong, f"hot reads off their index: {wrong}"
    return f"{len(plans)} hot reads on their indexes over 20 000-row relations"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_publication_is_content_addressed_and_immutable,
    check_refs_resolve_only_for_their_provider, check_a_stale_grant_stops_access_and_scheduling,
    check_leases_are_fenced_and_one_result_per_case,
    check_run_and_checkpoint_states_follow_the_contract,
    check_checkpoint_delivery_is_received_once, check_the_relay_redelivers_a_lost_acknowledgment,
    check_two_publishers_race_to_one_version, check_a_kill_around_commit_recovers_once,
    check_large_fixture_queries_use_their_indexes)}


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
def test_d7(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
