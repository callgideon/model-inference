#!/usr/bin/env python3
"""E6L-O5 / WR-B-5 (amended) / WR-OBS-1 / WR-I6-3 / WR-I7-1: `python -m infrx.lab.workers <role>`.

    uv run --frozen pytest -q tests/w/test_lab_workers.py

The Lab worker entry point of the I5/I6/I7/I2L-OBS units. Service-free: the stores are built
and never connected (a DSN nothing listens on), the object store is injected, every product
call is recorded by a fake. The real process runs twice: its refusal with nothing configured
and an unknown role. The eval role on real D7 + a synthetic dev endpoint is E6L j09's
(`tests/integration/lab_evaluate/scenarios_workers.py`).

Failure oracles: a role that starts without a setting it needs (or prints a value); the eval
role composed apart from the consumer worker's `lab_eval` (two compositions drift), over the
wrong sources, or reaching a deployment that is not the run provider's private dev revision
at the revision its ref pins; a checkpoint delivery not decided by B3 or not handed back at
capacity; a judge that is live by default or egresses past the local fake; a datasets pass
that skips a provider or a page; an emergency rollback without an operator or for a policy
read outside its provider; a readiness probe that is up with the database down or a pass
dead; a SIGTERM that is not a clean exit.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import socket
import subprocess
import sys
import types
from datetime import datetime, timezone

import httpx
import pytest

from infrx.config import RuntimeMisconfigured
from infrx.contracts import errors
from infrx.contracts.v2 import fixtures as v2fix
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.worker import __main__ as worker_main

API = pathlib.Path(__file__).resolve().parents[2]
DSN = "postgresql://infrx:do-not-print@127.0.0.1:9/infrx"      # nothing listens on 9
KEY = "provider-dev-secret-do-not-print"
NEMO = "b0000001-0000-4000-8000-000000000001"
BASE = {"LAB_DATABASE_URL": DSN, "LAB_WORKER_HEALTH_PORT": "18012"}
LAB = {**BASE, "LAB_S3_BUCKET": "infrx-lab"}
TRACES = {"CLICKHOUSE_URL": "http://ch.invalid:8123/infrx", "S3_TRACE_BUCKET": "infrx-traces"}
ENV = {"eval": {**LAB, "LAB_EVAL_ENDPOINT_URL": "http://127.0.0.1:9",
                "LAB_EVAL_ENDPOINT_KEY": KEY},
       "checkpoints": dict(LAB),
       "judge": {**BASE, "JUDGE_PROVIDER_URL": "http://127.0.0.1:9", **TRACES},
       "annotation": {**LAB, "LAB_TEACHER_URL": "http://127.0.0.1:9"}, "training": dict(LAB),
       "rollout": {**LAB, "LAB_OPERATOR_ID": "00000090-0000-4000-8000-000000000090"},
       "datasets": {**LAB, **TRACES}}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def outcome(call):
    """What `call()` returns, or the exception it raised (a mutant's crash is a failed
    assertion here, never a broken runner)."""
    try:
        return call()
    except Exception as died:              # noqa: BLE001 - the outcome is compared
        return died


def composed(role, env=None, **kw):
    return lab_workers.compose(role, ENV[role] if env is None else env,
                               objects=InMemoryObjectStore(), **kw)


class Retention:
    """T3's `Retention` as the Lab roles read it."""

    feedback = None

    @staticmethod
    def clock():
        return datetime(2026, 9, 28, tzinfo=timezone.utc)


@pytest.fixture
def no_trace_stack(monkeypatch):
    """ClickHouse and the trace bucket are never reached: the retention is recorded."""
    built = {}

    def trace_retention(limits, endpoint_url, objects=None, connect=None):
        built.update(limits=limits, endpoint_url=endpoint_url, objects=objects, connect=connect)
        return Retention()
    monkeypatch.setattr(lab_workers, "trace_retention", trace_retention)
    return built


# ------------------------------------------------------------------------ settings + process
@pytest.mark.parametrize("role", sorted(ENV))
def test_lab_workers__each_role_refuses_to_start_naming_a_missing_setting(role, no_trace_stack):
    """Every setting a role reads is required and named when absent; a value is never
    printed. The health port is the unit's (`-e LAB_WORKER_HEALTH_PORT`)."""
    for name in ENV[role]:
        env = {k: v for k, v in ENV[role].items() if k != name}
        with pytest.raises(Exception) as refused:
            composed(role, env)
        assert type(refused.value) is RuntimeMisconfigured, (role, name, refused.value)
        assert name in str(refused.value) and KEY not in str(refused.value)
        assert "do-not-print" not in str(refused.value)


def test_lab_workers__the_process_refuses_an_unknown_role_and_a_missing_setting():
    """The real `python -m infrx.lab.workers`: exit 2 naming the problem, never a value."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "JUDGE_"))}
    for argv, expected in ((["nope"], "nope"), (["eval"], "LAB_DATABASE_URL"),
                           ([], "role")):
        done = subprocess.run([sys.executable, "-m", "infrx.lab.workers", *argv], cwd=API,
                              env={**env, "LAB_EVAL_ENDPOINT_KEY": KEY}, capture_output=True,
                              text=True, timeout=60)
        assert done.returncode == 2, (argv, done.stderr)
        assert expected in done.stderr and KEY not in done.stderr + done.stdout
    refused = outcome(lambda: lab_workers.compose("nope", {}))
    assert type(refused) is RuntimeMisconfigured and "nope" in str(refused)


# ------------------------------------------------------------------------ eval (WR-B-5, WR-COMP)
def test_lab_workers__eval_is_the_consumer_workers_one_composition(monkeypatch):
    """E6L-O5: two entry points, one composition - the eval role is `infrx.worker`'s
    `lab_eval` (D7's outbox into `EvalRuns`, `lab_recover`), on the role's own database and
    Lab objects, with WR-COMP-1's evaluator source (D7's `lab_evaluator` for the provider the
    ref names) and WR-COMP-2's dev targets over L3's rows."""
    from infrx.state.lab_data import PgLabDataStore
    seen = {}

    def lab_eval(mode, connect, objects, evaluators, targets, worker_id):
        seen.update(mode=mode, connect=connect, objects=objects, evaluators=evaluators,
                    targets=targets, worker_id=worker_id)
        return {"lab_eval": "pump", "lab_recover": "recover"}
    monkeypatch.setattr(worker_main, "lab_eval", lab_eval)
    objects = InMemoryObjectStore()
    worker = lab_workers.compose("eval", ENV["eval"], objects=objects)
    assert worker.tasks == {"lab_eval": "pump", "lab_recover": "recover"}
    assert seen["objects"] is objects and seen["mode"] == "lab-eval"
    targets = seen["targets"]
    assert type(targets) is lab_workers.DevTargets
    assert (targets.url, targets.key) == ("http://127.0.0.1:9", KEY)
    assert targets.deployments._connect is seen["connect"]
    asked = []

    async def evaluator(self, ref, *, provider_org_id):
        asked.append((self._connect, ref, provider_org_id))
        return {"spec": ref}
    monkeypatch.setattr(PgLabDataStore, "evaluator", evaluator)
    ref = f"lab:evaluator:{NEMO}:e0000001-0000-4000-8000-000000000001@sha256:" + "0" * 64
    assert asyncio.run(seen["evaluators"](ref)) == {"spec": ref}
    assert asked == [(seen["connect"], ref, NEMO)]


class Deployments:
    def __init__(self, deployment) -> None:
        self.row = deployment

    async def deployment(self, deployment_revision_id):
        row = self.row
        return row if row is not None and row.deployment_revision_id == \
            deployment_revision_id else None


class Catalog:
    def __init__(self, serving, card) -> None:
        self.serving, self.card = serving, card

    async def serving_revision(self, serving_version_id):
        return self.serving if serving_version_id == self.serving.serving_version_id else None

    async def active_rate_card(self, deployment_revision_id):
        return self.card


def dev_world(**deployment):
    from infrx.lab.control.operations import serving_ref
    dev = v2fix.model("deployment_revision_private_dev.json").model_copy(update=deployment)
    serving, card = v2fix.model("serving_revision.json"), v2fix.model("rate_card_marlin.json")
    ref = serving_ref(v2fix.model("deployment_revision_private_dev.json"), serving)
    return lab_workers.DevTargets(Deployments(dev), Catalog(serving, card),
                                  url="http://gw.invalid", key=KEY), ref, dev, serving, card


def test_lab_workers__dev_targets_resolve_only_the_providers_private_dev_revision():
    """WR-COMP-2 without ControlReads: the ref's deployment (0032) must be the ref
    provider's private dev revision, serving exactly the revision the ref pins (L3's
    `serving_ref`); the call goes through the metering gateway with the endpoint credential,
    as the serving revision's model, priced by the deployment's active card. Anything else is
    not found (a run of another deployment is never sent); an unpriced one waits (503)."""
    from infrx.contracts.v2.records import Environment, Visibility
    from infrx.evaluation.runner import HttpDevEndpoint
    targets, ref, dev, serving, card = dev_world()
    endpoint, deployment = asyncio.run(targets(ref))
    assert type(endpoint) is HttpDevEndpoint and deployment == dev
    assert endpoint._url == "http://gw.invalid/v1/chat/completions" and endpoint._key == KEY
    assert endpoint._model == serving.public_model_id and endpoint._rate_card == card
    assert endpoint._client is targets.client
    other = "c0000003-0000-4000-8000-00000000000f"
    for wrong in (ref.replace(NEMO, other), ref[:-64] + "f" * 64,
                  ref.replace(dev.deployment_revision_id, other)):
        with pytest.raises(errors.NotFound):
            asyncio.run(targets(wrong))
    for change in ({"provider_org_id": other}, {"environment": Environment.prod},
                   {"visibility": Visibility.public}):
        broken = dev_world(**change)[0]
        with pytest.raises(errors.NotFound):
            asyncio.run(broken(ref))
    targets.catalog.card = None
    with pytest.raises(errors.DependencyUnavailable):
        asyncio.run(targets(ref))


# ------------------------------------------------------------------ checkpoints (WR-B-5 amended)
def test_lab_workers__checkpoints_compose_l3s_dev_deployer_and_the_lab_registry(monkeypatch):
    """WR-B3-3: with no sources given (the unit's `main()`), the role composes L3's dev
    deployer over `PgControlStore`'s reads (0044) on the role's pool and, per event, the Lab
    registry of the event's OWN provider over the role's Lab objects (`LAB_S3_BUCKET`)."""
    from infrx.evaluation import checkpoints
    from infrx.state.lab_control import PgControlStore
    fake_d8(monkeypatch, PgCheckpointLedger=Ledger)
    steps = captured_steps(monkeypatch)
    objects = InMemoryObjectStore()
    asyncio.run(objects.put_if_absent(f"lab/{NEMO}/c/a", b"mine", "x"))
    worker = lab_workers.compose("checkpoints", ENV["checkpoints"], objects=objects)
    worker.tasks["lab_checkpoints"]().close()
    handler = steps["lab checkpoints"][1].__self__.scheduler
    assert type(handler.deployer) is checkpoints.DevDeployer
    assert type(handler.deployer.reads) is PgControlStore
    assert handler.deployer.reads._connect is handler.ledger.connect
    seen = {}

    async def on_checkpoint(checkpoint_id, **kw):
        seen.update(kw)
    monkeypatch.setattr(checkpoints, "on_checkpoint", on_checkpoint)
    from infrx.state.lab_data import LabEvent
    asyncio.run(handler.enqueue(LabEvent(event_id="e", kind="checkpoint_received",
                                         provider_org_id=NEMO, payload={"checkpoint_id": "c"})))
    assert seen["deployer"] is handler.deployer
    assert asyncio.run(seen["registries"]["lab"](f"lab://{NEMO}/c/a")) == b"mine"
    assert asyncio.run(handler.registries("other")["lab"](f"lab://{NEMO}/c/a")) == b""


def fake_d8(monkeypatch, **classes):
    """0041-0043's module (lab-sql-lw3) as the composition imports it."""
    module = types.ModuleType("infrx.state.lab_pipeline")
    for name, value in classes.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, "infrx.state.lab_pipeline", module)


class Ledger:
    def __init__(self, connect) -> None:
        self.connect = connect

    async def event(self, checkpoint_id, *, provider_org_id):
        return object()                          # B3's signed event exists


def test_lab_workers__a_checkpoint_delivery_is_decided_by_b3_and_capacity_hands_it_back(
        monkeypatch):
    """The `checkpoint_received` relay over D7's outbox on the role's database: each
    delivery is B3's `on_checkpoint` for the event's own provider and checkpoint, over D8's
    checkpoint ledger (0042) and L2 on the same pool; another kind stays pending (never
    taken); `CapacityExhausted` reaches the relay, which hands the batch back."""
    from infrx.evaluation import checkpoints
    from infrx.state.lab_data import LabEvent, PgLabDataStore
    from infrx.state.outbox import OutboxRelay
    fake_d8(monkeypatch, PgCheckpointLedger=Ledger)
    registries, deployer, calls = {"s3": object()}, object(), []

    async def on_checkpoint(checkpoint_id, **kw):
        calls.append((checkpoint_id, kw))
        if checkpoint_id == "busy":
            raise errors.CapacityExhausted("at max_active")
        return {}
    monkeypatch.setattr(checkpoints, "on_checkpoint", on_checkpoint)
    steps = captured_steps(monkeypatch)
    worker = composed("checkpoints", registries=registries, deployer=deployer)
    worker.tasks["lab_checkpoints"]().close()
    interval, pump = steps["lab checkpoints"]
    relay = pump.__self__
    assert interval == worker_main.LAB_PUMP_S
    assert type(relay) is OutboxRelay and type(relay.store) is PgLabDataStore
    handler = relay.scheduler
    assert asyncio.run(handler.enqueue(LabEvent(event_id="e", kind="checkpoint_received",
                                                provider_org_id=NEMO,
                                                payload={"checkpoint_id": "c1"}))) is True
    (checkpoint, kw), = calls
    assert checkpoint == "c1" and kw["provider_org_id"] == NEMO
    assert (kw["registries"], kw["deployer"]) == (registries, deployer)
    assert kw["store"] is relay.store and type(kw["ledger"]) is Ledger
    assert kw["ledger"].connect is relay.store._connect
    assert kw["access"].store._connect is relay.store._connect
    with pytest.raises(errors.CapacityExhausted):
        asyncio.run(handler.enqueue(LabEvent(event_id="e2", kind="checkpoint_received",
                                             provider_org_id=NEMO,
                                             payload={"checkpoint_id": "busy"})))
    other = outcome(lambda: asyncio.run(handler.enqueue(LabEvent(
        event_id="e3", kind="eval_run", provider_org_id=NEMO, payload={"run_id": "r"}))))
    assert type(other) is errors.InvalidRequest
    assert len(calls) == 2
    handler.ledger = Unsigned()
    assert asyncio.run(handler.enqueue(LabEvent(event_id="e4", kind="checkpoint_received",
                                                provider_org_id=NEMO,
                                                payload={"checkpoint_id": "p3"}))) is True
    assert len(calls) == 2 and handler.ledger.asked == [("p3", NEMO)]


class Unsigned:
    """D8's checkpoint ledger without a signed event: a P3 (manual-bundle) checkpoint,
    decided by the Lab route, whose D7 receipt wrote the same `checkpoint_received` kind."""

    def __init__(self) -> None:
        self.asked = []

    async def event(self, checkpoint_id, *, provider_org_id):
        self.asked.append((checkpoint_id, provider_org_id))
        raise errors.NotFound("no such checkpoint event for this provider")


# ------------------------------------------------------------------------------ judge (WR-OBS-1)
def test_lab_workers__the_judge_is_j2_on_its_ledger_dry_run_by_default(no_trace_stack):
    """WR-J2-3 / WR-LSQ-INT-3: `JudgeWiring` over L2, D6J's `PgJudgeLedger` (0036), T3's
    retention of the trace stack, the approved rates and the role's settings - `dry_run`
    unless `JUDGE_MODE=live` with a budget. The provider adapter is J2's, which refuses any
    host but the local fake (P-10). Its one pass: silent `submitting` runs -> `ambiguous`."""
    from infrx.judge.cost import APPROVED_RATES
    from infrx.judge.submit import HttpJudgeProvider, JudgeWiring
    from infrx.lab.access import LabAccess
    from infrx.state.lab_consent import PgJudgeLedger
    worker = composed("judge")
    wiring = worker.wiring
    assert type(wiring) is JudgeWiring and wiring.settings.judge_mode == "dry_run"
    assert type(wiring.ledger) is PgJudgeLedger and type(wiring.access) is LabAccess
    assert wiring.access.store._connect is wiring.ledger._connect
    assert type(wiring.provider) is HttpJudgeProvider and wiring.rates is APPROVED_RATES
    assert type(wiring.retention) is Retention
    assert no_trace_stack["limits"].clickhouse_url == ENV["judge"]["CLICKHOUSE_URL"]
    assert set(worker.tasks) == {"judge_sweep"}
    with pytest.raises(RuntimeMisconfigured, match="JUDGE_LIVE_BUDGET_USD"):
        composed("judge", {**ENV["judge"], "JUDGE_MODE": "live"})
    live = composed("judge", {**ENV["judge"], "JUDGE_MODE": "live",
                              "JUDGE_LIVE_BUDGET_USD": "5"})
    assert live.wiring.settings.judge_mode == "live"
    remote = outcome(lambda: composed("judge", {**ENV["judge"],
                                                "JUDGE_PROVIDER_URL": "https://judge.example"}))
    assert type(remote) is RuntimeMisconfigured and "JUDGE_PROVIDER_URL" in str(remote)


def test_lab_workers__the_judge_pass_sweeps_silent_submissions(monkeypatch, no_trace_stack):
    from infrx.state.lab_consent import PgJudgeLedger
    swept = []

    async def sweep(self, older_than_s):
        swept.append(older_than_s)
        return 1
    monkeypatch.setattr(PgJudgeLedger, "sweep", sweep)
    steps = captured_steps(monkeypatch)
    worker = composed("judge")
    worker.tasks["judge_sweep"]().close()
    interval, step = steps["judge sweep"]
    assert asyncio.run(step()) == 1
    assert swept == [lab_workers.JUDGE_SILENT_S] and interval == lab_workers.JUDGE_PASS_S


def captured_steps(monkeypatch) -> dict:
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, (interval_s, step)), never())[1])
    return steps


def test_lab_workers__the_judge_report_job_publishes_each_configuration_on_its_ledger(
        no_trace_stack):
    """WR-J3-D8-C: the judge role's report job is J3's `publish` on the role's own
    `PgJudgeLedger` (the sweep's), once per configuration, the grantor's report stored under
    (provider, grantor, judge model, rubric version); one configuration's failure does not
    skip the next (it is counted)."""
    from infrx.judge.calibration import MIN_PAIRS, report

    from tests.j import fakes as j1
    from tests.j.calibration.test_calibration import agreeing, labelled
    worker = composed("judge")
    assert set(worker.jobs) == {"judge_report"}
    job = worker.jobs["judge_report"]
    assert job.ledger is worker.wiring.ledger
    stored = []

    class Ledger:
        async def put_calibration(self, calibration, **key):
            if key["judge_model"] == "broken":
                raise ConnectionError("the ledger did not answer")
            stored.append(key)
    job.ledger = Ledger()
    results, labels = labelled(agreeing(MIN_PAIRS))
    base = {"results": results, "feedback": labels, "provider_org_id": j1.ORG_B,
            "org_id": j1.ORG_A, "rubric_version": 1}
    done = outcome(lambda: asyncio.run(job([{**base, "judge_model": "broken"},
                                            {**base, "judge_model": "j-1"}])))
    assert done == {"published": 1, "failed": 1}
    assert stored == [{"provider_org_id": j1.ORG_B, "grantor_org_id": j1.ORG_A,
                       "judge_model": "j-1", "rubric_version": 1}]
    assert report(results, labels, org_id=j1.ORG_A, rubric_version=1).calibration()[
        "state"] == "calibrated"


# ------------------------------------------------------------------ datasets (WR-N3-2 pull)
def test_lab_workers__datasets_reconcile_every_providers_lineage_page_by_page(monkeypatch,
                                                                             no_trace_stack):
    """WR-N3-2's pull half: every provider with a lineage in the Lab objects, every page
    (the cursor followed to its end), over L2's grants on the role's database and T3's
    retention, writing 0041 through `PgSampleRestrictions` on the same login (WR-DS5-2:
    explicit, not found through the directory); one provider's failure does not skip the
    others."""
    from infrx.datasets import lineage
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_content import PgSampleRestrictions
    objects, calls = InMemoryObjectStore(), []
    for provider in ("p1", "p2", "p3"):
        asyncio.run(objects.put_if_absent(f"lab/{provider}/lineage/samples/s.json", b"{}",
                                          "application/json"))
    asyncio.run(objects.put_if_absent("lab/p4/datasets/x.json", b"{}", "application/json"))

    async def reconcile(directory, retention, objects_, *, provider_org_id, after=None,
                        restrictions=None):
        calls.append((provider_org_id, after))
        assert type(directory) is PgAccessStore and type(retention) is Retention
        # WR-DS5-2: 0041's restrictions passed explicitly, on the directory's own login
        assert type(restrictions) is PgSampleRestrictions, restrictions
        assert restrictions._connect is directory._connect
        assert objects_ is objects
        if provider_org_id == "p2":
            raise errors.DependencyUnavailable("clickhouse")
        return {"next": "k1" if after is None and provider_org_id == "p1" else None}
    monkeypatch.setattr(lineage, "reconcile", reconcile)
    steps = captured_steps(monkeypatch)
    worker = lab_workers.compose("datasets", ENV["datasets"], objects=objects)
    worker.tasks["lineage_reconcile"]().close()
    interval, step = steps["lineage reconcile"]
    assert interval == lab_workers.LINEAGE_PASS_S
    report = outcome(lambda: asyncio.run(step()))
    assert calls == [("p1", None), ("p1", "k1"), ("p2", None), ("p3", None)]
    assert report == {"providers": 3, "failed": 1}
    assert no_trace_stack["objects"] is objects      # WR-N3-2a: its deletions push tombstones


# ------------------------------------------------------------------ rollout (WR-I7-1)
def listing(provider, n, state):
    from infrx.state.lab_rollout import Release, ReleaseListing
    return ReleaseListing(policy_id=f"pol-{provider}-{n}", provider_org_id=provider,
                          endpoint_id="e", policy_ref=f"ref-{provider}-{n}",
                          release=Release(state=state, fence=1, plan_digest="d",
                                          started_at=datetime(2026, 9, 29, tzinfo=timezone.utc)),
                          latest_decision=None)


def test_lab_workers__the_rollout_pass_steps_every_released_policy_on_its_stored_plan(
        monkeypatch):
    """WR-R2-3: every ROLLOUT_PASS_S the rollout role runs R2's `Controller.step` (over D9's
    `PgReleaseStore` and L3's serving control, acting as `LAB_OPERATOR_ID`) for every running
    or rolled-back D9 release (`releases_in`) of every provider with a release under
    `lab/<p>/releases/`, on the plan stored beside it (`plan.json`; R2 refuses one whose
    digest is not D9's) and D7's policy record. A running release is evaluated on R1's live
    aggregates; while they are unreadable (`DependencyUnavailable`) it is held, never
    evaluated on invented numbers. A rolled-back release only converges (R216: step, never the
    operator's stop). A release without its plan is held; one failure is counted and the
    next release still runs."""
    from infrx.rollouts import control as r2
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_rollout import PgReleaseStore
    from tests.r.control.test_control import plan
    objects, stepped, asked = InMemoryObjectStore(), [], []
    stored = plan().model_dump_json().encode()
    for key in ("lab/p1/releases/pol-p1-1/plan.json", "lab/p1/releases/pol-p1-2/plan.json",
                "lab/p1/releases/pol-p1-3/plan.json", "lab/p2/releases/pol-p2-1/plan.json"):
        asyncio.run(objects.put_if_absent(key, stored, "application/json"))
    asyncio.run(objects.put_if_absent("lab/p3/releases/pol-p3-1/other.json", b"{}", "x"))
    asyncio.run(objects.put_if_absent("lab/p4/datasets/x.json", b"{}", "x"))

    async def releases_in(self, states=(), *, provider_org_id):
        asked.append((self._connect, tuple(states), provider_org_id))
        return {"p1": [listing("p1", 1, "running"), listing("p1", 2, "rolled_back"),
                       listing("p1", 3, "running")],
                "p2": [listing("p2", 1, "running")],
                "p3": [listing("p3", 1, "running")]}.get(provider_org_id, [])

    async def resolve(self, ref, *, provider_org_id):
        if ref == "ref-p2-1":
            raise errors.NotFound("gone")
        return ("policy", ref, provider_org_id)

    async def step(self, policy, policy_ref, plan_, live, *, now, report=None, runs=None):
        stepped.append((self, policy, policy_ref, plan_, live, report, runs))
        return r2.Verdict("hold", ())

    async def live(item):
        if item.policy_id == "pol-p1-3":
            raise errors.DependencyUnavailable("R1's aggregates")
        return ("live", item.policy_id)
    monkeypatch.setattr(PgReleaseStore, "releases_in", releases_in)
    monkeypatch.setattr(PgLabDataStore, "resolve", resolve)
    monkeypatch.setattr(r2.Controller, "step", step)
    steps = captured_steps(monkeypatch)
    worker = lab_workers.compose("rollout", ENV["rollout"], objects=objects, live=live)
    worker.tasks["rollout_pass"]().close()
    interval, pump = steps["rollout pass"]
    assert interval == lab_workers.ROLLOUT_PASS_S
    report = asyncio.run(pump())
    assert report == {"stepped": 2, "held": 2, "failed": 1}
    assert [p for _, _, p in asked] == ["p1", "p2", "p3"]
    assert {s for _, s, _ in asked} == {("running", "rolled_back")}
    (ctl, policy, ref, plan_, live_, rep, runs), (ctl2, _, ref2, _, live2, _, _) = stepped
    assert (policy, ref, plan_, live_) == (("policy", "ref-p1-1", "p1"), "ref-p1-1", plan(),
                                           ("live", "pol-p1-1"))
    assert (ref2, live2) == ("ref-p1-2", None) and (rep, runs) == (None, None)
    assert type(ctl._store) is PgReleaseStore and ctl._actor == ENV["rollout"]["LAB_OPERATOR_ID"]
    assert ctl._store._connect is asked[0][0] is ctl._serving.reads._connect
    assert ctl2 is ctl
    # without an injected source R1's aggregates are unreadable: every running release held
    stepped.clear()
    bare = lab_workers.compose("rollout", ENV["rollout"], objects=objects)
    bare.tasks["rollout_pass"]().close()
    assert asyncio.run(steps["rollout pass"][1]()) == {"stepped": 1, "held": 4, "failed": 0}
    assert [s[2] for s in stepped] == ["ref-p1-2"]


def test_lab_workers__an_emergency_rollback_is_r2s_for_the_named_operator(monkeypatch):
    """`rollout emergency-rollback --policy-ref --reason`: the operator is `LAB_OPERATOR_ID`
    of the invoking shell (required, named); the policy is D7's record of the provider the
    ref names; R2's `emergency_rollback` over D9 (`PgReleaseStore`) and L3's serving control
    runs once. A failure (the alias cannot converge before WR-LSQ-9) is a non-zero exit."""
    from infrx.rollouts import control
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_rollout import PgReleaseStore
    ref = f"lab:policy:{NEMO}:a0000000-0000-4000-8000-00000000006e@sha256:" + "1" * 64
    resolved, rolled = [], []

    async def resolve(self, policy_ref, *, provider_org_id):
        resolved.append((policy_ref, provider_org_id))
        return "policy-record"

    async def emergency_rollback(self, operator_id, policy, policy_ref, *, now, reason):
        rolled.append((operator_id, policy, policy_ref, reason, type(self._store),
                       type(self._serving), self._actor, self._serving.operator.principal,
                       type(self._serving.reads).__name__))
        if reason == "partitioned":
            raise errors.DependencyUnavailable("the alias did not converge")
    monkeypatch.setattr(PgLabDataStore, "resolve", resolve)
    monkeypatch.setattr(control.Controller, "emergency_rollback", emergency_rollback)
    argv = ["rollout", "emergency-rollback", "--policy-ref", ref, "--reason", "breach"]
    assert outcome(lambda: lab_workers.main(argv, env=dict(BASE))) == 2   # no operator
    assert rolled == []
    env = {**BASE, "LAB_OPERATOR_ID": "ops@infrx"}
    assert outcome(lambda: lab_workers.main(argv, env=env)) == 0
    assert resolved == [(ref, NEMO)]
    from infrx.lab.control.operations import Serving
    assert rolled == [("ops@infrx", "policy-record", ref, "breach", PgReleaseStore, Serving,
                       "ops@infrx", "ops@infrx", "PgControlStore")]
    assert outcome(lambda: lab_workers.main(argv[:-1] + ["partitioned"], env=env)) == 1


# ------------------------------------------------------------ annotation / training (WR-I6-3)
def test_lab_workers__training_has_no_pass_and_a_teacher_host_needs_its_approval():
    """P3's runs are started by the Lab route and the manual bundle has no platform job, so
    the training role refuses by name; a non-default adapter without its approval never
    composes, and the annotation role's teacher is the local fake only (P-10)."""
    with pytest.raises(RuntimeMisconfigured, match="manual-bundle"):
        composed("training")
    with pytest.raises(RuntimeMisconfigured, match="P-10"):
        composed("annotation", {**ENV["annotation"], "LAB_ANNOTATION_TEACHER": "hosted"})
    with pytest.raises(RuntimeMisconfigured, match="P-11"):
        composed("training", {**ENV["training"], "LAB_TRAINING_CONNECTOR": "http"})
    remote = outcome(lambda: composed("annotation", {
        **ENV["annotation"], "LAB_TEACHER_URL": "https://teacher.example/do-not-print"}))
    assert type(remote) is RuntimeMisconfigured and "LAB_TEACHER_URL" in str(remote)
    assert "P-10" in str(remote) and "do-not-print" not in str(remote)


def test_lab_workers__the_annotation_role_collects_teacher_batches_with_n2s_redaction(
        monkeypatch):
    """WR-P4B-2 / composition-4: the annotation role is P2's `TeacherWiring` on the role's
    database (`teacher_wiring`), N2's public redaction (WR-P2-4), the pilot settings from its
    environment (judge mode not live by default) and the local teacher fake
    `LAB_TEACHER_URL` names; its one pass is `collect_teachers` over that wiring, every
    `TEACHER_PASS_S`."""
    from infrx.datasets.versions import redact_content
    from infrx.judge.submit import HttpJudgeProvider
    from infrx.pipelines.teachers import TeacherWiring
    from infrx.state.lab_pipeline import PgTeacherLedger
    seen = []

    async def collect_teachers(wiring):
        seen.append(wiring)
        return {"collected": 0, "failed": 0}
    monkeypatch.setattr(lab_workers, "collect_teachers", collect_teachers)
    steps = captured_steps(monkeypatch)
    worker = composed("annotation")
    assert set(worker.tasks) == {"teacher_collect"}
    worker.tasks["teacher_collect"]().close()
    interval, step = steps["teacher collect"]
    assert interval == lab_workers.TEACHER_PASS_S
    asyncio.run(step())
    wiring = worker.wiring
    assert seen == [wiring] and type(wiring) is TeacherWiring
    assert type(wiring.ledger) is PgTeacherLedger and wiring.redact is redact_content
    assert (type(wiring.provider), wiring.provider.base_url) == (HttpJudgeProvider,
                                                                 "http://127.0.0.1:9")
    assert wiring.settings.judge_mode != "live"


def test_lab_workers__the_teacher_pass_collects_every_submitted_run_of_every_approved_batch(
        monkeypatch):
    """WR-P4B-2: the pass reads the Lab's durable teacher batches (`lab/<p>/teacher-batches/
    <id>/batch.json`, write-once, R202) and collects only APPROVED ones (an approval record
    beside the batch): each chunk run P2 plans for the batch whose ledger state (D8's
    `PgTeacherLedger`) is `submitted` is P2's `collect` (it imports the labels, records the
    per-item failures and settles once) - never an unreserved, completed or ambiguous run.
    The batch is the approver's (the actor who authorized the spend). One run's failure is
    counted; the next still runs."""
    import dataclasses
    import json
    from types import SimpleNamespace

    from infrx.pipelines import teachers as p2
    objects = InMemoryObjectStore()
    b1, b2, b3 = (f"b{n}000000-0000-4000-8000-000000000001" for n in (1, 2, 3))

    def put(key, body):
        asyncio.run(objects.put_if_absent(key, json.dumps(body).encode(), "application/json"))

    def batch(batch_id):
        return {"batch_id": batch_id, "dataset_ref": "d", "rubric_ref": "r",
                "teacher_model": "m", "prompt_version": "v", "payer_ref": "pay",
                "budget_usd": "1.00000000", "chunk_size": 2, "requested_by": "dev"}
    for provider, batch_id, approved in (("p1", b1, True), ("p1", b2, False),
                                         ("p2", b3, True)):
        put(f"lab/{provider}/teacher-batches/{batch_id}/batch.json", batch(batch_id))
        if approved:
            put(f"lab/{provider}/teacher-batches/{batch_id}/approval.json",
                {"approved_by": "admin", "approved_at": "2026-09-29T00:00:00Z"})
    put("lab/p1/datasets/x.json", {})
    chunks = {b1: ("r1", "r2", "r3", "r4"), b2: ("r6",), b3: ("r5",)}
    states = {"r1": "submitted", "r2": "completed", "r3": None, "r4": "submitted",
              "r5": "submitted", "r6": "submitted", "r7": "ambiguous"}
    chunks[b1] += ("r7",)
    calls = []

    async def plan(batch, *, store, objects, rates, now):
        return SimpleNamespace(chunks=tuple((r, ()) for r in chunks[batch.batch_id]))

    async def collect(batch, run_id, *, wiring):
        calls.append((batch.provider_org_id, batch.batch_id, batch.requested_by, run_id))
        if run_id == "r1":
            raise errors.DependencyUnavailable("the teacher did not answer")

    class Ledger:
        async def db_now(self):
            return datetime(2026, 9, 29, tzinfo=timezone.utc)

        async def run(self, run_id):
            return None if states[run_id] is None else SimpleNamespace(state=states[run_id])
    monkeypatch.setattr(p2, "plan", plan)
    monkeypatch.setattr(p2, "collect", collect)
    wiring = dataclasses.replace(composed("annotation").wiring, ledger=Ledger(), objects=objects)
    done = outcome(lambda: asyncio.run(lab_workers.collect_teachers(wiring)))
    assert calls == [("p1", b1, "admin", "r1"), ("p1", b1, "admin", "r4"),
                     ("p2", b3, "admin", "r5")]
    assert done == {"collected": 2, "failed": 1}


def test_lab_workers__the_teacher_wiring_is_p2_on_d8s_teacher_ledger():
    """WR-P2-D8-C (1-LSI2-2): P2's `collect` records per-item failures with
    `ledger.record_failures`, which only D8's `PgTeacherLedger` has (a plain `PgJudgeLedger`
    dies with AttributeError at the first failure). The composition puts it, P1's import over
    D8's label log, D7 and L2 on the role's one database, J2's provider to the local teacher
    fake only (P-10), and N2's `redact` as given (the role refuses without it, WR-P2-4)."""
    from infrx.contracts.limits import DEFAULTS
    from infrx.judge.cost import APPROVED_RATES
    from infrx.judge.submit import HttpJudgeProvider
    from infrx.pipelines import annotations as p1
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_pipeline import PgLabelLog, PgTeacherLedger
    connect, objects = connector(DSN), InMemoryObjectStore()

    def redact(text):
        return text
    wiring = lab_workers.teacher_wiring(connect, objects, provider_url="http://127.0.0.1:9",
                                        settings=DEFAULTS, redact=redact)
    assert type(wiring.ledger) is PgTeacherLedger and callable(wiring.ledger.record_failures)
    assert (type(wiring.members), type(wiring.store), type(wiring.log)) == (
        PgAccessStore, PgLabDataStore, PgLabelLog)
    assert {id(port._connect) for port in (wiring.members, wiring.ledger, wiring.store,
                                           wiring.log)} == {id(connect)}
    assert wiring.labels is p1.import_labels and wiring.objects is objects
    assert (wiring.redact, wiring.rates, wiring.settings) == (redact, APPROVED_RATES, DEFAULTS)
    assert type(wiring.provider) is HttpJudgeProvider
    remote = outcome(lambda: lab_workers.teacher_wiring(
        connect, objects, provider_url="https://teacher.example", settings=DEFAULTS,
        redact=redact))
    assert isinstance(remote, errors.DomainError), remote


# ----------------------------------------------------------------------- health and the drain
def test_lab_workers__readyz_is_the_database_and_every_pass_alive():
    """`/livez` 200 while every pass runs; `/readyz` 200 only while the database answers too;
    `/metrics` names the role; anything else 404. Loopback only."""
    state = {"db": True}

    async def ready():
        return state["db"]

    async def body():
        stop, port = asyncio.Event(), free_port()
        worker = lab_workers.Worker(role="eval", tasks={"p": lambda: asyncio.Event().wait()},
                                    ready=ready)
        serving = asyncio.create_task(lab_workers.serve(worker, port, stop=stop))
        for _ in range(100):
            await asyncio.sleep(0.02)
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            answers = [(await client.get("/livez")).status_code,
                       (await client.get("/readyz")).status_code]
            state["db"] = False
            answers.append((await client.get("/readyz")).status_code)
            metrics = (await client.get("/metrics")).text
            answers.append((await client.get("/nope")).status_code)
        stop.set()
        return answers, metrics, await serving
    answers, metrics, code = asyncio.run(body())
    assert answers == [200, 200, 503, 404] and code == 0
    assert 'infrx_lab_worker_up{role="eval"} 1' in metrics


def test_lab_workers__a_dead_pass_is_not_live_and_exits_non_zero():
    """A pass that ended (a raising rollout pass, a bug) is a failed process: `Restart=` of
    the unit restarts it; leases expire and are recovered."""
    async def dies():
        raise RuntimeError("pass failed")

    async def ready():
        return True

    async def body():
        worker = lab_workers.Worker(role="rollout", tasks={"pass": dies}, ready=ready)
        return await lab_workers.serve(worker, free_port())
    assert asyncio.run(body()) == 1


def test_lab_workers__the_pumps_are_every_step_forever():
    """The Lab passes use the consumer worker's `every` (a failed step is logged and
    retried) - one loop helper, not two."""
    assert lab_workers.every is worker_main.every


def test_lab_workers__trace_retention_is_t3s_over_the_shippers_bucket_and_bounds(monkeypatch):
    """The judge and datasets roles read content through T3's `Retention` over the ClickHouse
    client, the trace bucket at `build_shipper`'s prefix (else `read_content` finds nothing)
    and the pilot's content/metadata bounds. Service-free: both connections are recorded."""
    import inspect

    import clickhouse_connect

    from infrx.media.s3 import S3ObjectStore
    from infrx.traces.ship.shipper import build_shipper
    seen = {}
    monkeypatch.setattr(clickhouse_connect, "get_client",
                        lambda **kw: seen.setdefault("client", kw) and "ch-client")
    monkeypatch.setattr(S3ObjectStore, "connect", classmethod(
        lambda cls, *a: seen.setdefault("bucket", a) and InMemoryObjectStore()))
    limits = types.SimpleNamespace(clickhouse_url=TRACES["CLICKHOUSE_URL"],
                                   s3_trace_bucket=TRACES["S3_TRACE_BUCKET"],
                                   trace_content_max_days=13, trace_metadata_months=7)
    built = lab_workers.trace_retention(limits, "http://minio.invalid:9000")
    shipper_prefix = inspect.signature(build_shipper).parameters["prefix"].default
    assert seen["client"] == {"dsn": TRACES["CLICKHOUSE_URL"]}
    assert seen["bucket"] == (TRACES["S3_TRACE_BUCKET"], shipper_prefix,
                              "http://minio.invalid:9000") and shipper_prefix == "infrx/"
    assert (built.content_days, built.metadata_months) == (13, 7)
    assert isinstance(built.objects, InMemoryObjectStore)


def test_lab_workers__a_trace_deletion_tombstones_every_providers_lineage_copies(monkeypatch):
    """WR-N3-2a (the push half): T3's deletion on the Lab's retention calls N3's
    `lineage.tombstone` (signature frozen) for the deleted request, for every provider with a
    lineage, page by page while `more`, reason `deleted` at the tombstone's instant; a repeat
    deletion is the first receipt and pushes nothing; a push that fails never loses the
    receipt (the hourly pull tombstones it). The judge role's retention, with no Lab objects,
    pushes nothing."""
    import clickhouse_connect

    from infrx.datasets import lineage
    from infrx.media.s3 import S3ObjectStore
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kw: "ch-client")
    monkeypatch.setattr(S3ObjectStore, "connect",
                        classmethod(lambda cls, *a: InMemoryObjectStore()))
    calls, broken = [], []

    async def tombstone(objects, **kw):
        if broken:
            raise ConnectionError("the Lab bucket did not answer")
        calls.append(kw)
        return {"tombstoned": 1, "more": sum(c["provider_org_id"] == kw["provider_org_id"]
                                             for c in calls) < 2}
    monkeypatch.setattr(lineage, "tombstone", tombstone)

    class Store:
        stones: dict = {}

        async def get(self, keys):
            return {k: {"request": self.stones[k]} for k in keys if k in self.stones}

        async def put(self, stones):
            self.stones.update({(s.org_id, s.request_id): s for s in stones})
    objects = InMemoryObjectStore()
    for key in ("lab/p1/lineage/samples/a.json", "lab/p2/lineage/traces/g/r/b",
                "lab/p3/exports/e/export.json"):
        objects.seed(key, b"{}")
    limits = types.SimpleNamespace(clickhouse_url="http://ch", s3_trace_bucket="t",
                                   trace_content_max_days=13, trace_metadata_months=7)
    retention = lab_workers.trace_retention(limits, "", objects)
    retention.store = Store()
    stone = asyncio.run(retention.delete("g", "r", "user_request"))
    one = {"grantor_org_id": "g", "request_id": "r", "reason": "deleted",
           "at": stone.deleted_at}
    assert calls == [{"provider_org_id": p, **one} for p in ("p1", "p1", "p2", "p2")]
    assert asyncio.run(retention.delete("g", "r", "again")) == stone and len(calls) == 4
    broken.append(True)
    kept = outcome(lambda: asyncio.run(retention.delete("g", "r2", "user_request")))
    assert getattr(kept, "request_id", kept) == "r2"
    assert lab_workers.trace_retention(limits, "").deleted is None
