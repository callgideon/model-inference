#!/usr/bin/env python3
"""AP-10 10a (API-EVAL, the read half): `infrx.lab.evaluation`'s ports for
`/lab/v1/evaluations` over fakes with 0043's semantics - an authorized empty read is 200
empty, a database failure 503, a stored experiment readable with its launch, a resubmit the
first launch, another launch under the id a conflict; the catalog listing an honest 503.
The real SQL half is `test_evaluation_ports_pg.py` (ap10's PostgreSQL).

    uv run --frozen pytest -q tests/ap10/test_evaluation_ports.py
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.contracts.lab import records as lab
from infrx.evaluation.reports import _digest
from infrx.gateway.routes import lab_evaluations as le
from infrx.lab import evaluation as ev

from ..b.runner.world import NEMO, OUTSIDER, OTHER, SERVING, access, uid
from ..g import support
from ..g.lab_evaluations.test_lab_evaluations import (CANDIDATE, DEV, EVALUATOR, PROTOCOL,
                                                      Sessions, token)

E = uid(1, 0xe0)
T1, T2 = "2026-10-01T10:00:00Z", "2026-10-01T11:00:00Z"
LAUNCH = {"experiment_id": E, "dataset_ref": f"lab:dataset:{NEMO}:{uid(1, 0xda)}@sha256:" + "d" * 64,
          "harness_ref": f"lab:harness:{NEMO}:{uid(1, 0xa7)}@sha256:" + "a" * 64,
          "evaluator_ref": EVALUATOR, "baseline_serving_ref": SERVING,
          "candidate_serving_ref": CANDIDATE, "seed": 7, "max_cases": 3,
          "run_limit": {"unit": "CREDIT", "value": "10.00000000"}, "protocol": PROTOCOL}


def run(coro):
    return asyncio.run(coro)


class Store:
    """D7's records as `Experiments` uses them: content-addressed publish, resolve."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    async def publish(self, payload, *, provider_org_id, actor):
        ref = lab.ref_of(payload)
        self.records.setdefault(ref, payload)
        return ref

    async def resolve(self, ref, *, provider_org_id):
        return lab.parse(self.records[ref])

    async def evaluator(self, ref, *, provider_org_id):
        return {"ref": ref, "provider": provider_org_id}


class Reads:
    """0043: `lab_put_experiment` (write once; a replay is the row, another body a conflict)
    and `lab_experiments` (newest first; a run's status only once `freeze` created it)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.created: set[str] = set()          # run refs `freeze` created
        self.reports: dict[str, dict] = {}
        self.fail: Exception | None = None

    async def put_experiment(self, experiment_id, *, provider_org_id, protocol,
                             protocol_digest, baseline_run_ref, candidate_run_ref, actor):
        row = {"provider": provider_org_id, "protocol": protocol, "digest": protocol_digest,
               "refs": (baseline_run_ref, candidate_run_ref), "by": actor}
        have = self.rows.setdefault(experiment_id, row)
        if {k: have[k] for k in row if k != "by"} != {k: row[k] for k in row if k != "by"}:
            raise errors.IdempotencyConflict("this experiment id holds another launch")

    async def experiments(self, *, provider_org_id):
        if self.fail:
            raise self.fail
        return [{"experiment_id": eid, "protocol": row["protocol"],
                 "protocol_digest": row["digest"], "report": self.reports.get(eid),
                 **{arm: {"run_id": "r", "run_ref": ref} if ref in self.created else None
                    for arm, ref in zip(le.ARMS, row["refs"])}}
                for eid, row in reversed(self.rows.items())
                if row["provider"] == provider_org_id]

    def freeze(self, experiment_id: str) -> None:
        self.created.update(self.rows[experiment_id]["refs"])


def world():
    store, reads = Store(), Reads()
    return store, reads, ev.Experiments(store, reads)


def put(x, launch=LAUNCH, created_at=T1, actor=DEV):
    return run(x.put(NEMO, {"experiment_id": launch["experiment_id"], "created_at": created_at,
                            "launch": launch, "report": None}, actor=actor))


# ------------------------------------------------------------------------ experiments
def test_ap10_an_experiment_is_stored_once_as_its_two_run_records_and_a_resubmit_is_the_first():
    """WR-B4-2 on 0043: `put` publishes both arms' run records - the payloads `freeze`
    publishes next - and stores the pair under the launch's protocol digest; a resubmit
    with a later clock is the first launch with its first `created_at`, so `freeze` rebuilds
    the same two records; the listing reads the launch back from those records."""
    store, reads, x = world()
    first = put(x)
    refs = [lab.ref_of(le._run_payload(NEMO, le.Launch.model_validate(LAUNCH), arm, T1))
            for arm in le.ARMS]
    assert reads.rows[E] == {"provider": NEMO, "protocol": PROTOCOL, "digest": _digest(PROTOCOL),
                             "refs": tuple(refs), "by": DEV}
    assert sorted(store.records) == sorted(refs)
    assert (first["created_at"], first["launch"], first["report"]) == (T1, LAUNCH, None)
    reads.freeze(E)
    again = put(x, created_at=T2)
    assert (again["created_at"], le.Launch.model_validate(again["launch"])) == \
        (T1, le.Launch.model_validate(LAUNCH))
    assert sorted(store.records) == sorted(refs), "a resubmit publishes nothing new"
    (listed,) = run(x.experiments(NEMO))
    assert listed == again and listed["launch"] == LAUNCH
    assert run(x.experiments(OTHER)) == []


def test_ap10_another_launch_under_the_id_is_a_conflict_even_when_it_won_the_race():
    """Another launch under a stored id is `IdempotencyConflict`; two first launches racing
    on one id: the loser's insert conflicts (its clock made other records), it reads the
    winner's row and answers it when the launch is the same, a conflict when it is not."""
    store, reads, x = world()
    put(x)
    reads.freeze(E)
    with pytest.raises(errors.IdempotencyConflict):
        put(x, {**LAUNCH, "seed": 8}, created_at=T2)

    class Racing(ev.Experiments):
        seen = 0

        async def _stored(self, provider, experiment_id):
            self.seen += 1                      # the loser looked before the winner wrote
            return None if self.seen == 1 else await super()._stored(provider, experiment_id)
    loser = Racing(store, reads)
    won = put(loser, created_at=T2)
    assert (won["created_at"], loser.seen) == (T1, 2)
    with pytest.raises(errors.IdempotencyConflict):
        put(Racing(store, reads), {**LAUNCH, "max_cases": 2}, created_at=T2)


def test_ap10_a_launch_without_its_user_or_its_run_refs_is_an_honest_503():
    """Nothing is published without the session user (WR-AP10-2); an experiment stored
    before `freeze` created its runs has no refs in 0043's listing (SR-AP10-1): a resubmit
    is a 503 naming that, never a second launch, and the listing leaves it out."""
    store, reads, x = world()
    with pytest.raises(errors.DependencyUnavailable, match="WR-AP10-2"):
        put(x, actor=None)
    assert store.records == {} and reads.rows == {}
    put(x)
    with pytest.raises(errors.DependencyUnavailable, match="SR-AP10-1"):
        put(x, created_at=T2)
    assert run(x.experiments(NEMO)) == [] and len(store.records) == 2


def test_ap10_the_listed_report_is_b2s_stored_body_with_its_digest():
    """0043's `{report_digest, body}` is B2's report as stored (the release read's shape)."""
    store, reads, x = world()
    put(x)
    reads.freeze(E)
    body = {"schema": "infrx.eval_report.1", "decision": {"outcome": "accept", "reasons": []}}
    reads.reports[E] = {"report_digest": "sha256:" + "f" * 64, "body": json.dumps(body)}
    (listed,) = run(x.experiments(NEMO))
    assert listed["report"] == {**body, "report_digest": "sha256:" + "f" * 64}


# ------------------------------------------------------------------- ledger, catalog
def test_ap10_the_subscription_listing_is_flattened_and_the_rest_is_d8s():
    class Ledger:
        async def listing(self, *, provider_org_id):
            return [{"subscription": {"subscription_id": "s", "policy": "every"},
                     "decisions": [{"checkpoint_id": "c", "run_id": None}]}]

        async def add_subscription(self, sub):
            return ("added", sub)
    x = ev.Subscriptions(Ledger())
    assert run(x.listing(NEMO)) == [{"subscription_id": "s", "policy": "every",
                                     "decisions": [{"checkpoint_id": "c", "run_id": None}]}]
    assert run(x.add_subscription("sub")) == ("added", "sub")


def test_ap10_the_catalog_listing_is_503_naming_its_request_and_the_evaluator_is_d7s():
    x = ev.Catalog(Store())
    with pytest.raises(errors.DependencyUnavailable, match="SR-AP10-1"):
        run(x.catalog(NEMO))
    assert run(x.evaluator(NEMO, EVALUATOR)) == {"ref": EVALUATOR, "provider": NEMO}


def test_ap10_the_ports_share_one_pool():
    ports = ev.evaluation_ports("pool")
    assert set(ports) == {"experiments", "ledger", "catalog"}
    x = ports["experiments"]
    assert x.store._connect == x.reads._connect == "pool"
    assert ports["ledger"].ledger._connect == ports["catalog"].store._connect == "pool"
    assert type(x.reads).__name__ == "PgLabReads"
    assert type(ports["ledger"].ledger).__name__ == "PgCheckpointLedger"


# ----------------------------------------------------------------------- the routes
def client(reads=None, ledger_rows=()):
    store, reads = Store(), reads or Reads()

    class Ledger:
        async def listing(self, *, provider_org_id):
            return list(ledger_rows)
    lab_access = access()
    lab_access.store.provider_names = {NEMO: "Nemo", OTHER: "Other"}
    x = le.LabEvaluations(Sessions((DEV, OUTSIDER)), lab_access, store=store,
                          experiments=ev.Experiments(store, reads),
                          ledger=ev.Subscriptions(Ledger()), catalog=ev.Catalog(store))
    app = FastAPI()
    le.register(app, support.runtime(), x)
    return TestClient(app, raise_server_exceptions=False)


def get(c, path, user=DEV, provider=NEMO):
    return c.get(f"{le.EVALS_PREFIX}/{path}", params={"provider_org_id": provider},
                 headers={"authorization": f"Bearer {token(user)}"})


def test_ap10_authorized_empty_reads_are_200_empty_and_a_failed_read_is_503():
    """API-EVAL: with the ports composed, an authorized member with nothing reads 200 `[]`
    (before AP-10: 503 "experiments is not wired"); the database failing is 503, never an
    empty 200; the catalog is the honest 503; a stranger is refused before any read."""
    c = client()
    for path in ("experiments", "runs", "subscriptions"):
        answer = get(c, path, OUTSIDER, OTHER)
        assert (answer.status_code, answer.json()) == (200, {"data": []}), path
        assert answer.headers["cache-control"] == "no-store"
    assert get(c, "catalog").status_code == 503
    assert get(c, "experiments", OUTSIDER).status_code == 404
    broken = Reads()
    broken.fail = OSError("connection refused")
    c = client(broken)
    for path in ("experiments", "runs"):
        answer = get(c, path)
        assert (answer.status_code, answer.json()) == (503, {"refusal": "unavailable"}), path
