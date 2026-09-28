#!/usr/bin/env python3
"""D7: the Python half of `PgLabDataStore`, with NO database - what it sends to the RPCs of
`0029_lab_data.sql` and how it shapes their answers. `code_mutants_d7.py`'s Python list runs
here; the SQL is `test_d7_lab_data.py`.

    uv run --frozen pytest -q tests/d/test_d7_units.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.contracts.v2.records import AccessGrant
from infrx.lab.access import DatasetUse
from infrx.state.lab_data import LabEvent, PgLabDataStore, grant_ref

from .test_adapter_units import _Conn, _db_error, _refused

NEMO, OTHER = "b0000001-0000-4000-8000-000000000001", "b0000009-0000-4000-8000-000000000009"
ORG, GRANT = "0a000000-0000-4000-8000-00000000000a", "90000000-0000-4000-8000-000000000001"
HARNESS = {"schema": "lab.harness_revision.1", "provider_org_id": NEMO,
           "harness_id": "000000a0-0000-4000-8000-000000000014", "version": 1,
           "created_at": "2026-09-27T10:00:00Z", "adapter": "text", "prompt_template": "é {{x}}",
           "processor_profile": "p1", "input_mapping": {"x": "sample.text"}, "tools": []}
LEASE = {"provider_org_id": NEMO, "run_id": "r", "case_id": "c", "attempt": 1, "worker_id": "w"}


def _store(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgLabDataStore(connect), conn


def _sent(conn, n: int = 0) -> tuple[str, dict]:
    sql, params = conn.sent[n]
    return sql.split("infrx.")[1].split("(")[0], params[0].obj


def _ok(coro):
    """Any exception is the assertion it stands for (R40: a mutant dies on an assertion)."""
    try:
        return asyncio.run(coro)
    except Exception as failed:
        raise AssertionError(f"{type(failed).__name__}: {failed}") from None


def test_grant_ref__names_the_recipient_and_the_version() -> None:
    grant = AccessGrant.model_validate({
        "grant_id": GRANT, "version": 3, "grantor_org_id": ORG,
        "recipient_provider_org_id": NEMO, "model_ids": ["m"], "categories": ["feedback"],
        "purposes": ["training"], "retention_days": 30, "effective_at": "2026-09-27T00:00:00Z"})
    digest = hashlib.sha256(f'{{"grant_id":"{GRANT}","version":3}}'.encode()).hexdigest()
    assert grant_ref(grant) == f"lab:grant:{NEMO}:{GRANT}@sha256:{digest}", grant_ref(grant)


def test_publish__sends_the_canonical_bytes_of_the_callers_own_record() -> None:
    store, conn = _store({"ref": "lab:harness:x"})
    assert _ok(store.publish(HARNESS, provider_org_id=NEMO, actor="dev")) == "lab:harness:x"
    assert _sent(conn) == ("lab_publish", {"provider_org_id": NEMO, "actor": "dev",
                                           "body": records.canonical(HARNESS).decode()})
    _refused(errors.Forbidden, store.publish(HARNESS, provider_org_id=OTHER, actor="dev"))
    _refused(records.LabRejected, store.publish({**HARNESS, "harness_id": "not-a-uuid"},
                                                provider_org_id=NEMO, actor="dev"))
    assert len(conn.sent) == 1, "a refused record reached the database"


def test_resolve__is_the_parsed_record_and_a_refusal_is_typed() -> None:
    store, conn = _store({"ref": "r", "body": json.dumps(HARNESS)},
                         _db_error("P0001", "not_found: no such Lab record for this provider"))
    got = _ok(store.resolve("r", provider_org_id=NEMO))
    assert isinstance(got, records.HarnessRevision) and got.harness_id == HARNESS["harness_id"]
    assert _sent(conn) == ("lab_resolve", {"provider_org_id": NEMO, "ref": "r"})
    _refused(errors.NotFound, store.resolve("r", provider_org_id=OTHER))


def test_calls__carry_the_callers_provider_the_lease_and_the_cost() -> None:
    store, conn = _store({"ref": "s"}, ["x"], {}, {}, {}, None, {}, {}, {"expired": 2}, {}, {})
    cost = {"unit": "CREDIT", "value": "0.25000000"}
    assert _ok(store.register_source(provider_org_id=NEMO, source_id="s1", content_digest="d",
                                     grant_ref="g", actor="dev")) == "s"
    assert _ok(store.accessible_samples("d", provider_org_id=NEMO, purpose="training")) == ["x"]
    _ok(store.create_run("rr", provider_org_id=NEMO))
    _ok(store.run_status("r", provider_org_id=NEMO))
    _ok(store.cancel_run("r", provider_org_id=NEMO))
    assert _ok(store.lease_case("r", provider_org_id=NEMO, worker_id="w", lease_s=30)) is None
    _ok(store.heartbeat(LEASE, lease_s=60))
    _ok(store.finish(LEASE, outcome="succeeded", results=[], cost=cost))
    assert _ok(store.recover()) == 2
    _ok(store.receive_checkpoint(provider_org_id=NEMO, checkpoint_id="k",
                                 external_run_ref="e", artifact_digest="a"))
    _ok(store.transition_checkpoint("k", "validated", provider_org_id=NEMO))
    assert [_sent(conn, n) for n in range(len(conn.sent))] == [
        ("lab_register_source", {"provider_org_id": NEMO, "source_id": "s1",
                                 "content_digest": "d", "grant_ref": "g", "actor": "dev"}),
        ("lab_accessible_samples", {"provider_org_id": NEMO, "dataset_ref": "d",
                                    "purpose": "training"}),
        ("lab_create_run", {"provider_org_id": NEMO, "run_ref": "rr"}),
        ("lab_run_status", {"provider_org_id": NEMO, "run_id": "r"}),
        ("lab_cancel_run", {"provider_org_id": NEMO, "run_id": "r"}),
        ("lab_lease_case", {"provider_org_id": NEMO, "run_id": "r", "worker_id": "w",
                            "lease_s": 30}),
        ("lab_heartbeat", {"lease": LEASE, "lease_s": 60}),
        ("lab_finish_attempt", {"lease": LEASE, "outcome": "succeeded", "results": [],
                                "cost": cost}),
        ("lab_recover", {}),
        ("lab_receive_checkpoint", {"provider_org_id": NEMO, "checkpoint_id": "k",
                                    "external_run_ref": "e", "artifact_digest": "a"}),
        ("lab_checkpoint_transition", {"provider_org_id": NEMO, "checkpoint_id": "k",
                                       "state": "validated"}),
    ]


def test_outbox__is_the_relays_store_half_with_the_claimant_on_every_ack() -> None:
    event = {"event_id": "e1", "kind": "eval_run", "provider_org_id": NEMO,
             "payload": {"run_id": "r"}}
    store, conn = _store([event], 1, None, None)
    got = _ok(store.dispatch_pending(limit=5, worker_id="relay-1", redelivery_s=30))
    assert got == [LabEvent("e1", "eval_run", NEMO, {"run_id": "r"})] and got[0].event_id == "e1"
    assert _ok(store.acknowledge_dispatch(("e1",), worker_id="relay-1")) == 1
    _ok(store.release_dispatch(["e2"]))
    _ok(store.record_dispatch_error("e3", "boom"))
    assert [_sent(conn, n) for n in range(4)] == [
        ("lab_outbox_pending", {"limit": 5, "worker_id": "relay-1", "redelivery_s": 30}),
        ("lab_outbox_ack", {"event_ids": ["e1"], "worker_id": "relay-1"}),
        ("lab_outbox_release", {"event_ids": ["e2"]}),
        ("lab_outbox_error", {"event_id": "e3", "error": "boom"})]


def test_followup__error_release_results_evaluators_reports_and_uses() -> None:
    """0034's calls: a failed attempt's error only when given, the 402 release, the results
    read, RFC 8785 evaluator specs, B2 reports checked against their own digest BEFORE the
    database, and `uses` as the port's `DatasetUse` records."""
    rep = {"schema": "infrx.eval_report.1", "é": 0.5}
    body = records.canonical(rep).decode()
    digest = "sha256:" + hashlib.sha256(records.canonical(rep)).hexdigest()
    use = {"grantor_org_id": ORG, "model_id": "m", "category": "feedback"}
    store, conn = _store({}, {}, {"state": "released"}, {"attempt_rows": []}, {"ref": "e"},
                         {"ref": "e", "body": '{"metric": "exact_match"}'},
                         {"report_digest": digest}, {"report_digest": digest, "body": body},
                         [use])
    spec = {"metric": "exact_match", "é": 1}
    _ok(store.finish(LEASE, outcome="failed", results=[], error="bound:requests"))
    _ok(store.finish(LEASE, outcome="failed", results=[]))
    assert _ok(store.release(LEASE)) == {"state": "released"}
    _ok(store.run_results("r", provider_org_id=NEMO))
    assert _ok(store.put_evaluator(spec, provider_org_id=NEMO, evaluator_id="i",
                                   actor="dev")) == "e"
    assert _ok(store.evaluator("e", provider_org_id=NEMO)) == {"metric": "exact_match"}
    assert _ok(store.put_eval_report({**rep, "report_digest": digest}, provider_org_id=NEMO,
                                     actor="b2")) == digest
    assert _ok(store.eval_report(digest, provider_org_id=NEMO)) == {**rep, "report_digest": digest}
    assert _ok(store.uses(NEMO, "d")) == (DatasetUse(**use),)
    _refused(errors.InvalidRequest, store.put_eval_report(
        {**rep, "report_digest": "sha256:" + "0" * 64}, provider_org_id=NEMO, actor="b2"))
    failed = {"lease": LEASE, "outcome": "failed", "results": [], "cost": None}
    assert [_sent(conn, n) for n in range(len(conn.sent))] == [
        ("lab_finish_attempt", {**failed, "error": "bound:requests"}),
        ("lab_finish_attempt", failed),
        ("lab_release_attempt", {"lease": LEASE}),
        ("lab_run_results", {"provider_org_id": NEMO, "run_id": "r"}),
        ("lab_put_evaluator", {"provider_org_id": NEMO, "evaluator_id": "i", "actor": "dev",
                               "body": records.canonical(spec).decode()}),
        ("lab_evaluator", {"provider_org_id": NEMO, "ref": "e"}),
        ("lab_put_eval_report", {"provider_org_id": NEMO, "actor": "b2", "body": body}),
        ("lab_eval_report", {"provider_org_id": NEMO, "report_digest": digest}),
        ("lab_dataset_uses", {"provider_org_id": NEMO, "dataset_ref": "d"})]


def test_variant__a_comparison_is_sent_as_its_canonical_bytes_and_read_back_whole() -> None:
    """WR-R3-2: the stored body is the RFC 8785 bytes (so its digest is the content's)."""
    import json

    from infrx.contracts.lab import records
    from infrx.state.lab_data import PgLabDataStore
    doc = {"schema": "infrx.variant_comparison.1", "outcome": "equivalent", "é": 1}
    conn = _Conn([{"comparison_digest": "sha256:d"}, [{"body": json.dumps(doc)}]])

    async def connect():
        return conn
    store = PgLabDataStore(connect)
    assert _ok(store.put_variant_comparison(doc, provider_org_id=NEMO, actor="r3")) == \
        "sha256:d"
    assert _ok(store.variant_comparisons("sha256:r", provider_org_id=NEMO)) == [doc]
    assert [_sent(conn, n) for n in range(2)] == [
        ("lab_put_variant_comparison", {"provider_org_id": NEMO, "actor": "r3",
                                        "body": records.canonical(doc).decode()}),
        ("lab_variant_comparisons", {"provider_org_id": NEMO, "report_digest": "sha256:r"})]
