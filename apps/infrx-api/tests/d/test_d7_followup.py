#!/usr/bin/env python3
"""D7 follow-up (wave-5 LW2, lab-sql): the D7 review minors F3-F7 and RSI-3, and the requests
WR-B-2 (error codes, evaluators, the results read, the 402 release), WR-B-7 (write-once
reports) and H1's `DatasetSources` port, on real PostgreSQL for `0034_lab_eval_followup.sql`,
composed with `PgLabDataStore` and the L2 port (`LabAccess.authorize`, R172).

World: test_d7_lab_data's. Each `check_*` is the check a mutant in `code_mutants_d7.py`'s
`FOLLOWUP` list must break; the rolled-back ones leave nothing behind, the reaper drill
commits its own run.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d7_followup.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.lab.access import LabAccess
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_data import PgLabDataStore

from . import checks_credit as cc
from . import pgharness
from . import test_d7_lab_data as t

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d7f"

NEMO, OTHER, uid, l2 = t.NEMO, t.OTHER, t.uid, t.l2
ok, refusal, rolled_back, call, W = t.ok, t.refusal, t.rolled_back, t.call, t.W
TABLES = ("lab_evaluators", "lab_eval_reports")
RPCS = ("lab_put_evaluator", "lab_evaluator", "lab_run_results", "lab_release_attempt",
        "lab_put_eval_report", "lab_eval_report", "lab_dataset_uses")
seed = t.seed


def fail(lease: dict, error: str | None = "bound:requests") -> dict:
    out = {"lease": lease, "outcome": "failed", "results": [],
           "cost": {"unit": "CREDIT", "value": "0.10000000"}}
    return {**out, "error": error} if error else out


def run_state(conn, run_id: str) -> str:
    return ok(conn, "lab_run_status", {"provider_org_id": NEMO, "run_id": run_id})["state"]


def revoke(conn) -> None:
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": t.C1,
                                         "grantor_org_id": l2.org(conn, t.C1),
                                         "recipient_provider_org_id": NEMO})


def report(conn, tag: int, outcome: str = "accept") -> dict:
    """A B2-shaped report comparing two of NEMO's run records, with its digest."""
    dataset = t.publish(conn, t.manifest(uid(1, tag), n=1, tag=tag))
    harness = t.publish(conn, t.harness(uid(3, tag)))
    runs = [t.publish(conn, t.eval_run(uid(10 + i, tag), dataset, harness)) for i in range(2)]
    protocol = {"confidence": 0.95, "margin": 0.01, "min_cases": 2,
                "metric_source": "deterministic_metric", "required_slices": {}}
    body = {"schema": "infrx.eval_report.1", "baseline_run": runs[0], "candidate_run": runs[1],
            "universe_digest": f"sha256:{'1' * 64}", "protocol": protocol,
            "protocol_digest": "sha256:" + hashlib.sha256(records.canonical(protocol)).hexdigest(),
            "estimates": {"overall": {"diff": 0.125, "low": -0.0078125, "high": 0.25}},
            "decision": {"outcome": outcome, "reasons": []}}
    return {**body, "report_digest": "sha256:" + hashlib.sha256(
        records.canonical(body)).hexdigest()}


def put_report(conn, rep: dict, provider: str = NEMO) -> dict:
    body = records.canonical({k: v for k, v in rep.items() if k != "report_digest"}).decode()
    return {"provider_org_id": provider, "actor": "b2", "body": body}


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads the evaluators or reports or executes the follow-up
    RPCs; the platform role reads them and writes only through the RPCs; both are
    immutable."""
    probes = [f"select count(*) from infrx.{name}" for name in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached Lab state: {reached}"
    unread = [n for n in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(n)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [n for n in TABLES if not (cc.refused_as(conn, "service", f"delete from infrx.{n}")
                                         or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    for sql in ("update infrx.lab_evaluators set published_by = 'x'",
                "delete from infrx.lab_evaluators"):
        got = cc.attempt(conn, sql)
        assert got is not None and got.startswith("23514"), f"{sql}: {got}"
    unguarded = [n for n in TABLES if not conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"infrx.{n}",)
    ).fetchone()[0]]
    assert not unguarded, f"row security is off on {unguarded}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; immutable"


def check_the_reaper_skips_a_locked_attempt(conn) -> str:
    """F3 (EVAL-DURABLE): the reaper never waits on an attempt whose finish is in flight -
    with one expired attempt locked by another transaction it expires the others at once and
    leaves that one to the next sweep (commits its own run)."""
    with conn.transaction():
        _, run = t.a_run(conn, n=2, tag=0xf3)
        leases = [t.lease(conn, run["run_id"], lease_s=1) for _ in range(2)]
    held = psycopg.connect(pgharness.dsn(conn.info.dbname))
    try:
        held.execute("select 1 from infrx.lab_eval_attempts where run_id = %s and case_id = %s "
                     "for update", (run["run_id"], leases[0]["case_id"]))
        with conn.transaction():
            t.advance(conn, 5)
            conn.execute("set local lock_timeout = '3s'")
            try:
                expired = call(conn, "lab_recover", {})["expired"]
            except psycopg.Error as failed:
                raise AssertionError(f"the reaper waited on a locked attempt: "
                                     f"{failed.sqlstate}") from None
            states = dict(conn.execute("select case_id::text, state from infrx.lab_eval_attempts "
                                       "where run_id = %s", (run["run_id"],)).fetchall())
    finally:
        held.close()
    assert expired == 1 and states == {leases[0]["case_id"]: "leased",
                                       leases[1]["case_id"]: "expired"}, (expired, states)
    return "one locked attempt skipped, the other expired, no wait"


@rolled_back
def check_a_run_whose_every_case_failed_fails(conn) -> str:
    """F4: a run ends `failed` when no case succeeded and `succeeded` when any did."""
    _, run = t.a_run(conn, n=2, tag=0xf4)
    for _ in range(2):
        ok(conn, "lab_finish_attempt", fail(t.lease(conn, run["run_id"])))
    assert run_state(conn, run["run_id"]) == "failed", "an all-failed run succeeded"
    _, mixed = t.a_run(conn, n=2, tag=0xe4)
    ok(conn, "lab_finish_attempt", fail(t.lease(conn, mixed["run_id"])))
    ok(conn, "lab_finish_attempt", t.finish(t.lease(conn, mixed["run_id"])))
    assert run_state(conn, mixed["run_id"]) == "succeeded"
    return "all failed -> failed; one done -> succeeded"


@rolled_back
def check_a_sample_without_its_source_is_refused(conn) -> str:
    """F5 (DATA-IMMUTABLE): a manifest whose sample names no source - or not as a string - is
    refused before anything is written, never published short of that sample."""
    for tag, spoil in ((0xf5, lambda s: s.pop("source_ref")),
                       (0xe5, lambda s: s.update(source_ref=7))):
        payload = t.manifest(uid(1, tag), n=3, tag=tag)
        spoil(payload["samples"][1])
        assert t.refused_publish(conn, payload) == "invalid_request", tag
        assert t.count(conn, "select count(*) from infrx.lab_records where object_id = %s",
                       uid(1, tag)) == 0
    return "a sample without a string source ref refuses the whole manifest"


@rolled_back
def check_a_revocation_stops_leasing_mid_run(conn) -> str:
    """F6 (DATA-RIGHTS): once the grant is revoked no further case of a running run is
    leased (`forbidden`), and nothing moved; a re-grant resumes it."""
    _, run = t.a_run(conn, n=3, tag=0xf6)
    first = t.lease(conn, run["run_id"])
    revoke(conn)
    assert refusal(conn, "lab_lease_case", {"provider_org_id": NEMO, "run_id": run["run_id"],
                                            "worker_id": "w2", "lease_s": 30}) == "forbidden"
    assert t.count(conn, "select count(*) from infrx.lab_eval_cases where run_id = %s and "
                   "state = 'leased'", run["run_id"]) == 1
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["provider_sharing"]))
    assert t.lease(conn, run["run_id"], worker="w2")["case_id"] != first["case_id"]
    return "revoked: no lease; re-granted: leasing resumes"


@rolled_back
def check_source_and_checkpoint_ids_are_per_provider(conn) -> str:
    """F7: another provider's source or checkpoint under the same id is its own row - no
    conflict that reveals or blocks the first - and the first provider's replays still
    answer its own."""
    got = ok(conn, "lab_register_source", {
        "provider_org_id": OTHER, "source_id": uid(1, 0x5c), "actor": "dev@other",
        "content_digest": f"sha256:{'9' * 64}", "grant_ref": W["other_grant"]})
    assert got["ref"].startswith(f"lab:source:{OTHER}:{uid(1, 0x5c)}@"), got
    assert ok(conn, "lab_register_source", {
        "provider_org_id": NEMO, "source_id": uid(1, 0x5c), "actor": "dev@nemo",
        "content_digest": f"sha256:{'1' * 64}", "grant_ref": W["grant"]})["ref"] == W["source"]
    dataset = t.publish(conn, t.manifest(uid(1, 0xf7), n=1, tag=0xf7))
    mine = t.publish(conn, t.external_run(uid(2, 0xf7), dataset))
    other_ds = t.publish(conn, t.manifest(uid(3, 0xf7), n=1, provider=OTHER, tag=0xe7,
                                          source=W["other_source"], grant=W["other_grant"]),
                         OTHER)
    theirs = t.publish(conn, {**t.external_run(uid(4, 0xf7), other_ds), "provider_org_id": OTHER,
                              "budget": {**t.external_run(uid(4, 0xf7), other_ds)["budget"],
                                         "payer_ref": t.PAYER.replace(NEMO, OTHER)}}, OTHER)
    checkpoint = uid(5, 0xf7)
    a = ok(conn, "lab_receive_checkpoint", {"provider_org_id": NEMO, "checkpoint_id": checkpoint,
                                            "external_run_ref": mine,
                                            "artifact_digest": f"sha256:{'a' * 64}"})
    b = ok(conn, "lab_receive_checkpoint", {"provider_org_id": OTHER,
                                            "checkpoint_id": checkpoint,
                                            "external_run_ref": theirs,
                                            "artifact_digest": f"sha256:{'b' * 64}"})
    assert (a["external_run_ref"], b["external_run_ref"]) == (mine, theirs), (a, b)
    again = ok(conn, "lab_receive_checkpoint", {"provider_org_id": NEMO,
                                                "checkpoint_id": checkpoint,
                                                "external_run_ref": mine,
                                                "artifact_digest": f"sha256:{'a' * 64}"})
    assert again == a, "a redelivery is not the same receipt"
    return "same source id and checkpoint id: one row per provider"


@rolled_back
def check_costs_are_in_a_unit_of_the_runs_budgets(conn) -> str:
    """RSI-3 / R159: an attempt's cost is in a unit one of the run's budgets names; a
    PROVIDER_USD cost on a CREDIT-budgeted run is refused, never converted."""
    _, run = t.a_run(conn, n=1, tag=0xf8)
    lease = t.lease(conn, run["run_id"])
    usd = {**t.finish(lease), "cost": {"unit": "PROVIDER_USD", "value": "0.25000000"}}
    assert refusal(conn, "lab_finish_attempt", usd) == "invalid_request"
    ok(conn, "lab_finish_attempt", t.finish(lease))
    return "PROVIDER_USD on a CREDIT run refused; CREDIT recorded"


@rolled_back
def check_a_failed_attempt_keeps_its_error_code(conn) -> str:
    """WR-B-2 (a): a failed attempt stores its error code (a retried finish with another
    code conflicts); a success carries none; a malformed code is refused."""
    _, run = t.a_run(conn, n=2, tag=0xf9)
    lease = t.lease(conn, run["run_id"])
    assert refusal(conn, "lab_finish_attempt", {**t.finish(lease), "error": "x"}) == \
        "invalid_request", "a success with an error"
    assert refusal(conn, "lab_finish_attempt", fail(lease, "Not A Code!")) == "invalid_request"
    ok(conn, "lab_finish_attempt", fail(lease))
    assert refusal(conn, "lab_finish_attempt", fail(lease, "dependency_unavailable")) == \
        "idempotency_conflict", "a retried finish rewrote the error"
    ok(conn, "lab_finish_attempt", fail(lease))
    rows = ok(conn, "lab_run_results", {"provider_org_id": NEMO,
                                        "run_id": run["run_id"]})["attempt_rows"]
    assert [r["error"] for r in rows] == ["bound:requests"], rows
    return "error stored on failure only; replay-stable"


@rolled_back
def check_evaluators_are_registered_specs_of_their_provider(conn) -> str:
    """WR-B-2 (b) / R167: an evaluator's ref is its id and the sha256 of its spec's RFC 8785
    bytes (B1's `evaluator_ref`); it resolves for its provider only; a run record naming an
    unregistered evaluator - or a result under one - is `not_found`."""
    spec = {**t.EVALUATOR_SPEC, "max_requests": 9}
    evaluator_id = uid(1, 0xfa)
    want = (f"lab:evaluator:{NEMO}:{evaluator_id}@sha256:"
            f"{hashlib.sha256(records.canonical(spec)).hexdigest()}")
    args = {"provider_org_id": NEMO, "evaluator_id": evaluator_id, "actor": "dev",
            "body": t.body(spec)}
    assert ok(conn, "lab_put_evaluator", args)["ref"] == want
    assert ok(conn, "lab_put_evaluator", args)["ref"] == want, "a replay"
    assert json.loads(ok(conn, "lab_evaluator", {"provider_org_id": NEMO, "ref": want})["body"]) \
        == spec
    assert refusal(conn, "lab_evaluator", {"provider_org_id": OTHER, "ref": want}) == "not_found"
    assert refusal(conn, "lab_put_evaluator", {**args, "body": "{not json"}) == "invalid_request"
    dataset = t.publish(conn, t.manifest(uid(2, 0xfa), n=1, tag=0xfa))
    harness = t.publish(conn, t.harness(uid(3, 0xfa)))
    unregistered = {**t.eval_run(uid(4, 0xfa), dataset, harness),
                    "evaluator_ref": want.replace(want[-64:], "0" * 64)}
    assert t.refused_publish(conn, unregistered) == "not_found"
    foreign = {**t.eval_run(uid(5, 0xfa), dataset, harness),
               "evaluator_ref": t.EVALUATOR.replace(NEMO, OTHER)}
    assert t.refused_publish(conn, foreign) == "not_found", "another provider's evaluator"
    registered = t.publish(conn, {**t.eval_run(uid(6, 0xfa), dataset, harness),
                                  "evaluator_ref": want})
    run = ok(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": registered})
    lease = t.lease(conn, run["run_id"])
    stray = {**t.finish(lease), "results": [{"evaluator_ref": want.replace(want[-64:], "1" * 64),
                                             "body": "{}"}]}
    assert refusal(conn, "lab_finish_attempt", stray) == "not_found"
    forged = cc.attempt(conn, "insert into infrx.lab_evaluators (ref, provider_org_id, "
                        "evaluator_id, body, published_by) values (%s, %s, %s, '{}', 'x')",
                        (want.replace(want[-64:], "2" * 64), NEMO, evaluator_id))
    assert forged is not None and "lab_evaluators_content_addressed" in forged, forged
    return "content-addressed, provider-scoped, required by runs and results"


@rolled_back
def check_the_results_read_is_the_runs_own(conn) -> str:
    """WR-B-2 (c): one read gives the run's status, every case's state, every attempt's cost
    and error, and every result - for its provider only."""
    _, run = t.a_run(conn, n=2, tag=0xfb)
    ok(conn, "lab_finish_attempt", t.finish(t.lease(conn, run["run_id"]), score=1))
    ok(conn, "lab_finish_attempt", fail(t.lease(conn, run["run_id"])))
    got = ok(conn, "lab_run_results", {"provider_org_id": NEMO, "run_id": run["run_id"]})
    assert [c["state"] for c in got["case_states"]] == ["done", "failed"], got["case_states"]
    assert [(a["state"], a["cost"], a["error"]) for a in got["attempt_rows"]] == [
        ("succeeded", {"unit": "CREDIT", "value": "0.25000000"}, None),
        ("failed", {"unit": "CREDIT", "value": "0.10000000"}, "bound:requests")], got
    assert [(r["evaluator_ref"], json.loads(r["body"])) for r in got["results"]] == \
        [(t.EVALUATOR, {"score": 1})], got["results"]
    assert got["state"] == "succeeded" and got["costs"] == {"CREDIT": "0.35000000"}, got
    assert refusal(conn, "lab_run_results", {"provider_org_id": OTHER,
                                             "run_id": run["run_id"]}) == "not_found"
    return "cases, attempts (cost, error), results; provider-scoped"


@rolled_back
def check_a_released_lease_consumes_no_attempt(conn) -> str:
    """WR-B-2 (d): a 402's lease is given back - the case is pending again with its attempt
    count restored, and the next lease is the SAME attempt number and key; a stale, foreign
    or repeated release is refused and changes nothing."""
    _, run = t.a_run(conn, n=1, tag=0xfc)
    lease = t.lease(conn, run["run_id"])
    assert refusal(conn, "lab_release_attempt", {"lease": {**lease, "worker_id": "w9"}}) == \
        "stale_lease"
    assert refusal(conn, "lab_release_attempt", {"lease": {**lease, "provider_org_id": OTHER}}) \
        == "not_found"
    released = ok(conn, "lab_release_attempt", {"lease": lease})
    assert released["state"] == "released", released
    case = conn.execute("select state, attempts from infrx.lab_eval_cases where run_id = %s",
                        (run["run_id"],)).fetchone()
    assert case == ("pending", 0), case
    assert refusal(conn, "lab_release_attempt", {"lease": lease}) == "stale_lease"
    again = t.lease(conn, run["run_id"], worker="w2")
    assert (again["attempt"], again["idempotency_key"]) == (1, lease["idempotency_key"]), again
    return "released: pending, attempt uncounted, same key next; stale refused"


@rolled_back
def check_reports_are_write_once_by_digest(conn) -> str:
    """WR-B-7: a B2 report is stored as its RFC 8785 bytes under the sha256 of them (its own
    `report_digest`); storing it again is the same digest and no second row; it must compare
    two of the provider's run records and be an `infrx.eval_report.1`; it reads back for its
    provider only and is never edited."""
    rep = report(conn, 0xfd)
    digest = ok(conn, "lab_put_eval_report", put_report(conn, rep))["report_digest"]
    assert digest == rep["report_digest"], (digest, rep["report_digest"])
    assert ok(conn, "lab_put_eval_report", put_report(conn, rep))["report_digest"] == digest
    assert t.count(conn, "select count(*) from infrx.lab_eval_reports") == 1
    back = ok(conn, "lab_eval_report", {"provider_org_id": NEMO, "report_digest": digest})
    assert json.loads(back["body"]) == {k: v for k, v in rep.items() if k != "report_digest"}
    assert refusal(conn, "lab_eval_report", {"provider_org_id": OTHER,
                                             "report_digest": digest}) == "not_found"
    assert refusal(conn, "lab_put_eval_report", put_report(conn, rep, OTHER)) == "not_found"
    assert refusal(conn, "lab_put_eval_report", put_report(conn, {**rep, "schema": "x"})) == \
        "invalid_request"
    got = cc.attempt(conn, "update infrx.lab_eval_reports set body = body")
    assert got is not None and got.startswith("23514"), got
    forged = cc.attempt(conn, "insert into infrx.lab_eval_reports select %s, provider_org_id, "
                        "baseline_run_ref, candidate_run_ref, protocol_digest, outcome, body, "
                        "stored_by from infrx.lab_eval_reports", (f"sha256:{'0' * 64}",))
    assert forged is not None and "lab_eval_reports_content_addressed" in forged, forged
    return "write-once by content digest; provider-bound"


@rolled_back
def check_dataset_uses_are_the_captured_grant_scope(conn) -> str:
    """H1 / R172: `DatasetSources.uses` is every (grantor, model, category) of the grant
    versions the provider's own dataset was captured under - not of a later version that
    widened the grant - and none for another provider."""
    dataset = t.publish(conn, t.manifest(uid(1, 0xfe), n=2, tag=0xfe))
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["provider_sharing"],
                                              categories=["request_content", "feedback"]))
    uses = ok(conn, "lab_dataset_uses", {"provider_org_id": NEMO, "dataset_ref": dataset})
    assert uses == [{"grantor_org_id": l2.org(conn, t.C1), "model_id": l2.MODEL,
                     "category": "request_content"}], uses
    assert ok(conn, "lab_dataset_uses", {"provider_org_id": OTHER, "dataset_ref": dataset}) == []
    return f"uses = the captured grant's scope: {len(uses)}"


def check_the_store_composes(conn) -> str:
    """`PgLabDataStore`'s follow-up methods over the RPCs, and H1's gate: `LabAccess`
    (PgAccessStore) with this store as its `DatasetSources` allows the developer on a
    current grant and denies after revocation (commits its own objects)."""
    dsn = pgharness.dsn(conn.info.dbname)
    store = PgLabDataStore(connector(dsn))
    access = LabAccess(PgAccessStore(connector(dsn)), datasets=store)
    with conn.transaction():
        dataset = t.publish(conn, t.manifest(uid(1, 0xef), n=1, tag=0xef))
        _, run = t.a_run(conn, n=1, tag=0xee)
    rep = report(conn, 0xed)

    async def go() -> list:
        got = [await store.put_evaluator(t.EVALUATOR_SPEC, provider_org_id=NEMO,
                                         evaluator_id=t.EVALUATOR_ID, actor="dev")]
        got.append(await store.evaluator(t.EVALUATOR, provider_org_id=NEMO))
        lease = await store.lease_case(run["run_id"], provider_org_id=NEMO, worker_id="w",
                                       lease_s=30)
        got.append((await store.release(lease))["state"])
        lease = await store.lease_case(run["run_id"], provider_org_id=NEMO, worker_id="w",
                                       lease_s=30)
        await store.finish(lease, outcome="failed", results=[], error="wallet_exhausted")
        got.append((await store.run_results(run["run_id"], provider_org_id=NEMO))[
            "attempt_rows"][0]["error"])
        digest = await store.put_eval_report(rep, provider_org_id=NEMO, actor="b2")
        got.append((await store.eval_report(digest, provider_org_id=NEMO)) == rep)
        try:
            await store.put_eval_report({**rep, "report_digest": f"sha256:{'0' * 64}"},
                                        provider_org_id=NEMO, actor="b2")
        except errors.InvalidRequest:
            got.append("forged digest refused")
        got.append([u.category for u in await store.uses(NEMO, dataset)])
        try:
            await access.authorize(records.Gate.schedule, user_id=l2.DEV,
                                   provider_org_id=NEMO, dataset_ref=dataset)
            got.append("authorized")
        except errors.Forbidden:
            got.append("forbidden")
        return got
    got = asyncio.run(go())
    with conn.transaction():
        revoke(conn)
    try:
        asyncio.run(access.authorize(records.Gate.schedule, user_id=l2.DEV, provider_org_id=NEMO,
                                     dataset_ref=dataset))
        got.append("allowed after revocation")
    except errors.Forbidden:
        got.append("denied after revocation")
    assert got == [t.EVALUATOR, t.EVALUATOR_SPEC, "released", "wallet_exhausted", True,
                   "forged digest refused", ["request_content"], "authorized",
                   "denied after revocation"], got
    return f"store round trip and H1's gate: {got[2:]}"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_the_reaper_skips_a_locked_attempt,
    check_a_run_whose_every_case_failed_fails, check_a_sample_without_its_source_is_refused,
    check_a_revocation_stops_leasing_mid_run, check_source_and_checkpoint_ids_are_per_provider,
    check_costs_are_in_a_unit_of_the_runs_budgets, check_a_failed_attempt_keeps_its_error_code,
    check_evaluators_are_registered_specs_of_their_provider,
    check_the_results_read_is_the_runs_own, check_a_released_lease_consumes_no_attempt,
    check_reports_are_write_once_by_digest, check_dataset_uses_are_the_captured_grant_scope,
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
def test_d7_followup(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
