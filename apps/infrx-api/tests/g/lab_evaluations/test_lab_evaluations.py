#!/usr/bin/env python3
"""WR-B4-1: `/lab/v1/evaluations` - LAB-ACCESS, EVAL-DURABLE and EVAL-COMPARE, the route half.

    uv run --frozen pytest -q tests/g/lab_evaluations

The backends are the merged Python over B3's fake world (`tests/b/checkpoints/world.py`: D7's
evaluation store, the checkpoint ledger, the L2 port): B1's `freeze` and B3's `subscribe` run
for real. D7 here cancels whatever the state, as `0029_lab_data.sql:lab_cancel_run`'s update does
without its state trigger, so the route's guard is proven alone (the real D7: `_pg.py`). The
tables not merged yet - experiments (WR-B4-2), the ledger's provider listing (WR-B3-1), the
catalog and evaluator specs (WR-LAB2-2) - are the small fakes below. The session verifier
is a minimal fake of `lab_auth.Sessions` (lab-api, batch #4).
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.evaluation import reports
from infrx.gateway.routes import lab_evaluations as le

from .. import support
from ...b.checkpoints import world as b3
from ...b.runner.world import (EVALUATOR_ID, OUTSIDER, SERVING, SPEC, VIEWER, eval_run,
                               uid)

P = le.EVALS_PREFIX
NEMO, OTHER, DEV = b3.NEMO, b3.OTHER, b3.DEV
CONSUMER = "f1000000-0000-4000-8000-0000000000f1"
EVALUATOR = le.runner.evaluator_ref(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID)
CANDIDATE = SERVING[:-64] + "6" * 64
EXPERIMENT = uid(1, 0xe0)
PROTOCOL = {"confidence": 0.95, "margin": 0.05, "min_cases": 2,
            "metric_source": "deterministic_metric",
            "required_slices": {"safety": {"margin": 0.0, "min_cases": 2}}}


def token(user: str) -> str:
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    """`lab_auth.Sessions`: a distinct JWT-shaped token per user."""

    def __init__(self, users) -> None:
        self.users = {token(u): u for u in users}

    async def user_id(self, value: str) -> str:
        if value not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[value]


class D7(b3.CheckpointStore):
    """0029's `lab_cancel_run`: `cancelled` whatever the run's state."""

    async def cancel_run(self, run_id, *, provider_org_id):
        self._run(run_id, provider_org_id)["state"] = "cancelled"
        return await self.run_status(run_id, provider_org_id=provider_org_id)


class Ledger(b3.FakeCheckpointLedger):
    """B3's ledger plus the provider listing WR-B3-1 asks of lab-sql: B3's subscription row
    (evaluator and owner included, as stored) with one decision per checkpoint."""

    async def listing(self, provider_org_id):
        return [{**s.model_dump(mode="json"), "decisions": [
            {"checkpoint_id": c, "step": 1, "receipt": "evaluated", **d}
            for (sid, c), d in self.decided.items() if sid == s.subscription_id]}
            for s in self.subs.values() if s.provider_org_id == provider_org_id]


class Experiments:
    """WR-B4-2: write once per (provider, experiment_id)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    async def put(self, provider_org_id, experiment):
        have = self.rows.setdefault((provider_org_id, experiment["experiment_id"]), experiment)
        if have["launch"] != experiment["launch"]:
            raise errors.IdempotencyConflict("the experiment id names another launch")
        return have

    async def experiments(self, provider_org_id):
        return [row for (p, _), row in self.rows.items() if p == provider_org_id]


class Catalog:
    def __init__(self, w) -> None:
        self.offered = {NEMO: {"datasets": [{"ref": w.dataset, "label": "d"}],
                               "harnesses": [{"ref": w.harness, "harness_id": uid(1, 0xa7),
                                              "version": 1, "adapter": "text"}],
                               "servings": [{"ref": SERVING, "label": "base"},
                                            {"ref": CANDIDATE, "label": "cand"}],
                               "evaluators": [{"ref": EVALUATOR, "label": "exact"}]}}
        self.calls: list[str] = []

    async def catalog(self, provider_org_id):
        self.calls.append("catalog")
        return self.offered.get(provider_org_id, {"datasets": [], "harnesses": [],
                                                  "servings": [], "evaluators": []})

    async def evaluator(self, provider_org_id, evaluator_ref):
        self.calls.append("evaluator")
        if (provider_org_id, evaluator_ref) != (NEMO, EVALUATOR):
            raise errors.NotFound("no such evaluator for this provider")
        return SPEC


class World(b3.World):
    def __init__(self) -> None:
        super().__init__()
        self.store.__class__ = D7
        self.ledger = Ledger()
        self.access.store.provider_names = {NEMO: "Nemo", OTHER: "Other"}
        self.experiments, self.catalog = Experiments(), Catalog(self)
        self.sessions = Sessions((DEV, VIEWER, OUTSIDER, CONSUMER))

    def evaluations(self, **absent) -> le.LabEvaluations:
        ports = {"store": self.store, "experiments": self.experiments, "ledger": self.ledger,
                 "catalog": self.catalog}
        return le.LabEvaluations(self.sessions, self.access, **{**ports, **absent})

    def client(self, *, on_runtime=False, **absent) -> TestClient:
        app, rt = FastAPI(), support.runtime()
        x = self.evaluations(**absent)
        if on_runtime:
            rt.lab_evaluations = x
            assert le.register(app, rt) is x
        else:
            assert le.register(app, rt, x) is x
        return TestClient(app, raise_server_exceptions=False)

    def launch(self, **over) -> dict:
        return {"experiment_id": EXPERIMENT, "dataset_ref": self.dataset,
                "harness_ref": self.harness, "evaluator_ref": EVALUATOR,
                "baseline_serving_ref": SERVING, "candidate_serving_ref": CANDIDATE,
                "seed": 7, "max_cases": 3,
                "run_limit": {"unit": "CREDIT", "value": "10.00000000"},
                "protocol": PROTOCOL, **over}

    def subscription(self, n: int = 1, **over) -> dict:
        body = super().subscription(n, **over)
        return {k: v for k, v in body.items() if k not in ("provider_org_id", "evaluator")}


def call(c, user, method, path, body=None, *, provider=NEMO, headers=None, raw=None):
    head = {"authorization": f"Bearer {token(user)}"} if user else {}
    head.update(headers or {})
    if raw is not None:
        head = {"content-type": "application/json", **head}
        return c.request(method, f"{P}/{path}", params={"provider_org_id": provider},
                         content=raw, headers=head)
    return c.request(method, f"{P}/{path}", params={"provider_org_id": provider}, json=body,
                     headers=head)


def routes(w):
    return (("GET", "catalog", None), ("GET", "runs", None), ("GET", "experiments", None),
            ("GET", "subscriptions", None),
            ("POST", f"runs/{le.run_id(EXPERIMENT, 'baseline')}/cancel", None),
            ("POST", "experiments", w.launch()), ("POST", "subscriptions", w.subscription()))


def untouched(w) -> bool:
    return (w.store.runs, w.experiments.rows, w.ledger.subs, w.catalog.calls) == ({}, {}, {}, [])


# --- mounting ------------------------------------------------------------------------------
def test_lab_evaluations__nothing_is_mounted_without_the_switch():
    """Oracle: no `rt.lab_evaluations` (LAB_EVALS off), no route - a 404, never a stand-in."""
    app = FastAPI()
    assert le.register(app, support.runtime()) is None
    assert not [r for r in app.routes if getattr(r, "path", "").startswith(P)]
    w = World()
    assert call(w.client(on_runtime=True), DEV, "GET", "catalog").status_code == 200


# --- LAB-ACCESS: identity first, the actor from the session ---------------------------------
def test_lab_evaluations__every_route_needs_the_session_before_anything_else():
    """Oracle: no token, or an API key, is a 401 on every route and nothing is asked - not even
    the body, so an invalid one is still a 401."""
    w = World()
    c = w.client()
    for method, path, body in routes(w):
        for headers in ({}, {"authorization": f"Bearer {support.TOKEN}"}):
            for sent in (body, {"forged": True}):
                answer = call(c, None, method, path, sent, headers=headers)
                assert (answer.status_code, answer.json()) \
                    == (401, {"refusal": "unauthenticated"}), path
    assert untouched(w)


def test_lab_evaluations__a_consumer_only_user_is_denied_and_another_provider_is_not_found():
    """Oracle (LAB-ACCESS): a user with no provider membership is a 403 on every route; a
    member of OTHER naming NEMO is a 404 on every route (a forged id confirms nothing);
    nothing is asked of the backends."""
    w = World()
    c = w.client()
    for method, path, body in routes(w):
        answer = call(c, CONSUMER, method, path, body)
        assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"}), path
        answer = call(c, OUTSIDER, method, path, body)
        assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"}), path
    assert untouched(w)


def test_lab_evaluations__every_role_reads_and_only_run_evaluation_writes():
    """Oracle: a viewer reads the four listings and is refused (403) launch and subscribe
    before any backend is asked, and the cancel of a run the provider has (B4-J02: the run is
    looked up first) without it moving; a developer's launch, subscribe and cancel succeed."""
    w = World()
    c = w.client()
    assert call(c, DEV, "POST", "experiments", w.launch()).status_code == 202
    w.catalog.calls.clear()
    runs, experiments = dict(w.store.runs), dict(w.experiments.rows)
    got = [call(c, VIEWER, m, path, body).status_code
           for m, path, body in routes(w)[:5] + ((routes(w)[5][0], routes(w)[5][1],
                                                   w.launch(experiment_id=uid(2, 0xe0))),
                                                  routes(w)[6])]
    assert got == [200, 200, 200, 200, 403, 403, 403]
    assert (w.store.runs, w.experiments.rows, w.ledger.subs) == (runs, experiments, {})
    assert w.catalog.calls == ["catalog"]              # no write reached a backend
    assert call(c, DEV, "POST", "subscriptions", w.subscription()).status_code == 201
    assert call(c, DEV, "POST", f"runs/{le.run_id(EXPERIMENT, 'baseline')}/cancel"
                ).status_code == 200


# --- EVAL-DURABLE: a launch is two D7 runs, once --------------------------------------------
def test_lab_evaluations__a_launch_is_two_d7_runs_and_a_resubmit_is_the_same_runs():
    """Oracle (EVAL-DURABLE): the launch answers 202 with two queued D7 runs whose ids derive
    from the experiment id, each the session user's published `lab.eval_run.1` with its arm's
    serving and the experiment's `created_at`; the same form again is the same experiment and
    the same two runs (no third run); another launch under that id is a 409."""
    w = World()
    c = w.client()
    first = call(c, DEV, "POST", "experiments", w.launch())
    body = first.json()
    assert first.status_code == 202 and isinstance(body, dict), body
    ids = [le.run_id(EXPERIMENT, arm) for arm in le.ARMS]
    assert [body[arm]["run_id"] for arm in le.ARMS] == ids
    assert [body[arm]["state"] for arm in le.ARMS] == ["queued", "queued"]
    assert (body["experiment_id"], body["protocol"], body["report"]) \
        == (EXPERIMENT, PROTOCOL, None)
    for arm, serving in zip(le.ARMS, (SERVING, CANDIDATE)):
        record = w.store.catalog.resolve(body[arm]["run_ref"], provider_org_id=NEMO)
        assert (record.run_id, record.serving_ref, record.created_at) \
            == (le.run_id(EXPERIMENT, arm), serving, body["created_at"])
    w.access.store.now += timedelta(hours=1)
    again = call(c, DEV, "POST", "experiments", w.launch())
    assert (again.status_code, again.json()) == (202, body)
    assert sorted(w.store.runs) == sorted(ids)
    other = call(c, DEV, "POST", "experiments", w.launch(seed=8))
    assert (other.status_code, other.json()) == (409, {"refusal": "conflict"})
    assert sorted(w.store.runs) == sorted(ids)


def test_lab_evaluations__a_launch_interrupted_between_the_freezes_resumes_as_the_same_runs():
    """Oracle (EVAL-DURABLE): the store fails the candidate's creation once - the call is a
    503 and the half-made experiment is not listed; the resubmit creates the candidate only,
    under its derived id and the experiment's first `created_at`."""
    w = World()
    c = w.client()
    create = w.store.create_run
    failures = [errors.DependencyUnavailable("lost")]

    async def flaky(run_ref, *, provider_org_id):
        if "candidate" in [arm for arm in le.ARMS
                           if le.run_id(EXPERIMENT, arm) in run_ref] and failures:
            raise failures.pop()
        return await create(run_ref, provider_org_id=provider_org_id)
    w.store.create_run = flaky
    assert call(c, DEV, "POST", "experiments", w.launch()).status_code == 503
    assert list(w.store.runs) == [le.run_id(EXPERIMENT, "baseline")]
    assert call(c, DEV, "GET", "experiments").json() == {"data": []}
    baseline = le.run_id(EXPERIMENT, "baseline")
    assert call(c, DEV, "GET", "runs").json()["data"] \
        == [b3.World._run(w.store.run_status(baseline, provider_org_id=NEMO))]
    created_at = w.experiments.rows[(NEMO, EXPERIMENT)]["created_at"]
    w.access.store.now += timedelta(minutes=5)
    answer = call(c, DEV, "POST", "experiments", w.launch())
    assert answer.status_code == 202 and answer.json()["created_at"] == created_at
    assert sorted(w.store.runs) == sorted(le.run_id(EXPERIMENT, arm) for arm in le.ARMS)
    for arm in le.ARMS:
        record = w.store.catalog.resolve(answer.json()[arm]["run_ref"], provider_org_id=NEMO)
        assert record.created_at == created_at, arm


def test_lab_evaluations__a_launch_is_checked_before_anything_is_written():
    """Oracle (B4-J02): a dataset, harness, serving or evaluator not in the provider's
    catalog (another provider's, or none) is a 422 like a run limit in another unit than
    CREDIT, a protocol with no margin rule or a body naming a provider or user; another
    provider's member launching this form reaches nothing of it (422) - and no experiment or
    run exists after any of them, nor was an evaluator spec asked for."""
    w = World()
    c = w.client()
    other = le.runner.evaluator_ref(SPEC, provider_org_id=NEMO, evaluator_id=uid(9, 0xee))
    answer = call(c, OUTSIDER, "POST", "experiments", w.launch(), provider=OTHER)
    assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    for body, status, reason in (
            (w.launch(evaluator_ref=other), 422, "invalid"),
            (w.launch(dataset_ref=w.dataset.replace(NEMO, OTHER)), 422, "invalid"),
            (w.launch(harness_ref=w.harness[:-64] + "0" * 64), 422, "invalid"),
            (w.launch(baseline_serving_ref=SERVING.replace(NEMO, OTHER)), 422, "invalid"),
            (w.launch(candidate_serving_ref=SERVING[:-64] + "8" * 64), 422, "invalid"),
            (w.launch(run_limit={"unit": "PROVIDER_USD", "value": "10.00000000"}), 422,
             "invalid"),
            (w.launch(protocol={**PROTOCOL, "confidence": 0.4}), 422, "invalid"),
            (w.launch(provider_org_id=OTHER), 422, "invalid"),
            (w.launch(user_id=OUTSIDER), 422, "invalid")):
        answer = call(c, DEV, "POST", "experiments", body)
        assert (answer.status_code, answer.json()) == (status, {"refusal": reason}), body
    assert (w.store.runs, w.experiments.rows) == ({}, {})
    assert "evaluator" not in w.catalog.calls           # refused by the listing, no spec asked


def test_lab_evaluations__a_launch_naming_what_the_provider_does_not_hold_is_invalid():
    """Oracle (R183, B4-J02): a launch form naming a ref the provider does not hold is a 422,
    never a 404 - an evaluator the catalog lists but holds no spec for, or a ref D7's publish
    cannot resolve (0034: an unregistered evaluator) - and no run exists after either."""
    w = World()
    c = w.client()
    unknown = le.runner.evaluator_ref(SPEC, provider_org_id=NEMO, evaluator_id=uid(9, 0xee))
    w.catalog.offered[NEMO]["evaluators"].append({"ref": unknown, "label": "gone"})
    answer = call(c, DEV, "POST", "experiments", w.launch(evaluator_ref=unknown))
    assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})

    async def unresolved(payload, *, provider_org_id, actor):
        raise errors.NotFound("no such evaluator for this provider")
    w.store.publish = unresolved
    answer = call(c, DEV, "POST", "experiments", w.launch(experiment_id=uid(2, 0xe0)))
    assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    assert w.store.runs == {} and call(c, DEV, "GET", "experiments").json() == {"data": []}


# --- cancel ---------------------------------------------------------------------------------
def test_lab_evaluations__cancel_stops_a_live_run_and_a_finished_run_is_a_conflict():
    """Oracle: cancelling a queued or running run is D7's `cancelled`; a succeeded, failed or
    already cancelled run is a 409 and keeps its state (this D7 alone would flip it); another
    provider's run is a 404."""
    w = World()
    c = w.client()
    call(c, DEV, "POST", "experiments", w.launch())
    base, cand = (le.run_id(EXPERIMENT, arm) for arm in le.ARMS)
    w.store.runs[cand]["state"] = "running"
    for rid in (base, cand):
        answer = call(c, DEV, "POST", f"runs/{rid}/cancel")
        assert (answer.status_code, answer.json().get("state")) == (200, "cancelled"), rid
    for state in ("succeeded", "failed", "cancelled"):
        w.store.runs[base]["state"] = state
        answer = call(c, DEV, "POST", f"runs/{base}/cancel")
        assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"}), state
        assert w.store.runs[base]["state"] == state
    answer = call(c, OUTSIDER, "POST", f"runs/{cand}/cancel", provider=OTHER)
    assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"})


def test_lab_evaluations__a_run_the_provider_does_not_have_is_not_found_whatever_the_role():
    """Oracle (LAB-ACCESS, B4-J02: existence is not confirmed by role): a viewer cancelling a
    run the provider does not have is a 404, as a developer is - never a 403 - and nothing
    moves."""
    w = World()
    c = w.client()
    for user in (VIEWER, DEV):
        answer = call(c, user, "POST", f"runs/{uid(9, 0xe1)}/cancel")
        assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"}), user
    assert w.store.runs == {}


# --- the listings ---------------------------------------------------------------------------
def test_lab_evaluations__runs_are_the_providers_experiment_and_subscription_runs_verbatim():
    """Oracle: `runs` is D7's `lab_run_status` of every run the provider's experiments and
    subscription decisions name, once each, verbatim; another provider sees none of them."""
    w = World()
    c = w.client()
    call(c, DEV, "POST", "experiments", w.launch())
    call(c, DEV, "POST", "subscriptions", w.subscription())
    checkpoint_run = b3.World._run(le.runner.freeze(
        w.store, eval_run(w.dataset, w.harness, run=9, max_cases=3), evaluator=SPEC,
        access=w.access, user_id=DEV, provider_org_id=NEMO)).run.run_id
    for n, (state, rid) in enumerate((("queued", checkpoint_run), ("skipped", None),
                                      ("queued", le.run_id(EXPERIMENT, "baseline"))), 1):
        w.ledger.decided[(uid(1, 0x5b), uid(n, 0xc3))] = {"state": state, "reason": None,
                                                          "run_id": rid}
    rows = call(c, VIEWER, "GET", "runs").json()["data"]
    expected = [b3.World._run(w.store.run_status(rid, provider_org_id=NEMO))
                for rid in (*(le.run_id(EXPERIMENT, arm) for arm in le.ARMS), checkpoint_run)]
    assert rows == expected
    assert call(c, OUTSIDER, "GET", "runs", provider=OTHER).json() == {"data": []}


def test_lab_evaluations__an_experiment_carries_b2s_report_verbatim():
    """Oracle (EVAL-COMPARE): until a report is stored the experiment's `report` is null; B2's
    report - here a hidden safety regression under an overall gain, with a missing and an
    errored case and CREDIT beside PROVIDER_USD - is served byte for byte, every slice,
    interval, reason and per-unit cost as B2 wrote it."""
    w = World()
    c = w.client()
    call(c, DEV, "POST", "experiments", w.launch())
    listed = call(c, VIEWER, "GET", "experiments").json()["data"]
    assert [e["report"] for e in listed] == [None]
    runs = [w.store.catalog.resolve(listed[0][arm]["run_ref"], provider_org_id=NEMO)
            .model_dump(mode="json", by_alias=True) for arm in le.ARMS]
    universe = [uid(i, 0xca) for i in range(1, 9)]

    def case(i, score, **extra):
        return {"case_id": universe[i], "score": score, "slices": ["safety"] if i < 3 else [],
                "cluster": universe[i], "latency_ms": 100 + i,
                "costs": [{"unit": "CREDIT", "value": "1.00000000"},
                          {"unit": "PROVIDER_USD", "value": "0.01000000"}], **extra}
    baseline = [case(i, 1.0 if i < 3 else 0.0) for i in range(8)]
    candidate = [case(i, 0.0 if i < 3 else 1.0) for i in range(7)]
    candidate[6] = case(6, 0.0, error="timeout")
    report = reports.compare(runs[0], baseline, runs[1], candidate, universe=universe,
                             protocol=PROTOCOL)
    w.experiments.rows[(NEMO, EXPERIMENT)]["report"] = report
    answer = call(c, VIEWER, "GET", "experiments")
    served = answer.json()["data"][0]["report"]
    assert json.dumps(served, sort_keys=True) == json.dumps(report, sort_keys=True)
    assert served["decision"]["outcome"] == "reject"
    assert served["observed"]["candidate"]["missing"] == 1
    assert set(served["observed"]["baseline"]["costs"]) == {"CREDIT", "PROVIDER_USD"}
    assert answer.headers.get("cache-control") == "no-store"


def test_lab_evaluations__subscribe_is_b3s_with_the_catalogs_evaluator_and_the_session_owner():
    """Oracle: the subscription B3 stores has the session user as owner and the evaluator
    spec the catalog holds for the ref; the answer and the listing never carry either; the
    same form again is the same subscription, another body under its id a 409, a PROVIDER_USD
    limit a 422 (B3's CREDIT-only rule)."""
    w = World()
    c = w.client()
    answer = call(c, DEV, "POST", "subscriptions", w.subscription())
    assert answer.status_code == 201
    stored = w.ledger.subs[uid(1, 0x5b)]
    assert (stored.owner_user_id, stored.evaluator, stored.provider_org_id) == (DEV, SPEC, NEMO)
    expected = {**w.subscription(), "provider_org_id": NEMO, "decisions": []}
    assert answer.json() == expected
    assert call(c, VIEWER, "GET", "subscriptions").json() == {"data": [expected]}
    again = call(c, DEV, "POST", "subscriptions", w.subscription())
    assert (again.status_code, again.json()) == (201, expected)
    other = call(c, DEV, "POST", "subscriptions", w.subscription(seed=8))
    assert (other.status_code, other.json()) == (409, {"refusal": "conflict"})
    usd = call(c, DEV, "POST", "subscriptions", w.subscription(
        2, limit="100", run_limit="10") | {"limit": {"unit": "PROVIDER_USD",
                                                     "value": "100.00000000"}})
    assert (usd.status_code, usd.json()) == (422, {"refusal": "invalid"})
    assert list(w.ledger.subs) == [uid(1, 0x5b)]


def test_lab_evaluations__a_subscription_naming_what_the_provider_does_not_hold_is_invalid():
    """Oracle (B4-J03): an external run of another provider, or an evaluator the catalog does
    not hold, is a 422 (the form is wrong, not a missing page), and nothing is subscribed."""
    w = World()
    c = w.client()
    foreign = w.external.replace(NEMO, OTHER)
    unknown = le.runner.evaluator_ref(SPEC, provider_org_id=NEMO, evaluator_id=uid(9, 0xee))
    for over in ({"external_run_ref": foreign}, {"evaluator_ref": unknown}):
        answer = call(c, DEV, "POST", "subscriptions", w.subscription(**over))
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"}), over
    assert w.ledger.subs == {}


# --- the ports not merged yet -----------------------------------------------------------------
@pytest.mark.parametrize("absent", ("store", "experiments", "ledger", "catalog"))
def test_lab_evaluations__an_unwired_port_is_unavailable_after_the_access_checks(absent, caplog):
    """Oracle: LAB_EVALS on before a table merges answers 503 on the calls that need it - an
    expected state, not a bug to log - and still 401/404 before it; the rest are served."""
    w = World()
    c = w.client(**{absent: None})
    caplog.set_level("ERROR")
    expected = {"store": (200, 503, 503, 200, 503, 503, 503),
                "experiments": (200, 503, 503, 200, 404, 503, 201),
                "ledger": (200, 503, 200, 503, 404, 202, 503),
                "catalog": (503, 200, 200, 200, 404, 503, 503)}[absent]
    for (method, path, body), wanted in zip(routes(w), expected):
        assert call(c, DEV, method, path, body).status_code == wanted, (absent, method, path)
        assert call(c, OUTSIDER, method, path, body).status_code == 404
    assert caplog.records == []


def test_lab_evaluations__a_body_is_json_bounded_and_valid_before_the_backends():
    """Oracle: a non-JSON content type, an oversized body or a malformed ref is a 422 and no
    backend is asked; the same body padded under the bound is accepted."""
    w = World()
    c = w.client()
    text = json.dumps(w.launch())
    for raw, content_type in ((text, "text/plain"),
                              (text + " " * le.lab_auth.MAX_BODY_BYTES, None)):
        answer = call(c, DEV, "POST", "experiments", raw=raw,
                      headers={"content-type": content_type} if content_type else None)
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    answer = call(c, DEV, "POST", "experiments", w.launch(dataset_ref="lab:dataset:x"))
    assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    assert untouched(w)
    assert call(c, DEV, "POST", "experiments", raw=text + " " * 64).status_code == 202
