"""WR-B4-1: `/lab/v1/evaluations`, the Lab's evaluation surface over D7, B1, B2's stored
reports and B3. Records are the Lab's `apps/lab/lib/services/evaluation/port.ts`: the
backends' own JSON, verbatim; lists are `{"data": ...}`.

    GET  /lab/v1/evaluations/catalog?provider_org_id=                -> {data: catalog}
    GET  /lab/v1/evaluations/runs?provider_org_id=                   -> {data: [D7 run status]}
    POST /lab/v1/evaluations/runs/{run_id}/cancel?provider_org_id=   -> D7 run status
    GET  /lab/v1/evaluations/experiments?provider_org_id=            -> {data: [experiment]}
    POST /lab/v1/evaluations/experiments?provider_org_id=            -> 202 experiment
    GET  /lab/v1/evaluations/subscriptions?provider_org_id=          -> {data: [subscription]}
    POST /lab/v1/evaluations/subscriptions?provider_org_id=          -> 201 subscription

As `/lab/v1/control` (`lab_auth`): every call re-derives the actor from the forwarded session
and the user's current membership before a body is read; every role reads
(`read_aggregate_health`), launch, cancel and subscribe need `run_evaluation`; nothing in a
body names a provider, user or role. Nothing executes in a request:

* **Launch** writes the experiment once (`ExperimentStore.put`, keyed by the form's
  `experiment_id`: the same launch again is the stored row, another launch under it a
  conflict), so its `created_at` - the store's clock - is fixed; then B1's `freeze` publishes
  and creates both D7 runs as the session user (B1 re-checks the membership, D7 the grants).
  Run ids derive from (experiment_id, baseline|candidate) and the records carry the stored
  `created_at`, so a resubmit - even after a crash between the two freezes - is the same two
  D7 runs, never a second paid pair. B1's workers run them; B2's `compare`, once both are
  terminal, is a worker's (WR-B-5) and its stored report is served verbatim.
* **Cancel**: D7's `lab_cancel_run` sets `cancelled` whatever the state, so the run is read
  first and a finished one is a 409.
* **Runs** are those of the provider's experiments and subscription decisions (D7 lists no
  runs); an experiment whose freeze was refused (no D7 run) is not listed.
* **Subscribe** is B3's `subscribe` with the evaluator spec the catalog holds for the ref;
  the same id under another body is a conflict.

A port without its table yet answers 503: experiments (WR-B4-2), the B3 ledger and its
provider listing (WR-B3-1), the catalog and evaluator specs (R167; WR-LAB-API-2-1). Mounted
only when the composition put a `LabEvaluations` on `rt.lab_evaluations` (LAB_EVALS, off).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from fastapi import Request
from pydantic import Field, ValidationError

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import ProviderCapability as Cap
from ...evaluation import checkpoints, runner
from ...evaluation.reports import Protocol as ComparisonProtocol
from .. import lab_auth
from . import intake
from .lab_control import Actor

EVALS_PREFIX = "/lab/v1/evaluations"
MAX_BODY_BYTES = 16_384
LIVE = ("queued", "running")
ARMS = ("baseline", "candidate")
PRIVATE = ("evaluator", "owner_user_id")         # B3's, never the Lab's


class Credit(lab.Amount):
    unit: Literal["CREDIT"]                 # a run spends provider_dev CREDIT (B1)


class Launch(lab.LabModel):
    experiment_id: lab.Uuid
    dataset_ref: lab.RefOf("dataset")
    harness_ref: lab.RefOf("harness")
    evaluator_ref: lab.RefOf("evaluator")
    baseline_serving_ref: lab.RefOf("serving")
    candidate_serving_ref: lab.RefOf("serving")
    seed: int = Field(ge=0)
    max_cases: int = Field(ge=1)
    run_limit: Credit
    protocol: ComparisonProtocol


class SubscriptionRequest(lab.LabModel):
    subscription_id: lab.Uuid
    external_run_ref: lab.RefOf("external_run")
    dataset_ref: lab.RefOf("dataset")
    harness_ref: lab.RefOf("harness")
    evaluator_ref: lab.RefOf("evaluator")
    seed: int = Field(ge=0)
    max_cases: int = Field(ge=1)
    run_limit: lab.Amount
    limit: lab.Amount
    max_active: int = Field(ge=1)
    policy: Literal["latest_only", "every"]


class ExperimentStore(Protocol):
    """WR-B4-2 (lab-sql): the provider's experiments."""

    async def put(self, provider_org_id: str, experiment: dict[str, Any]) -> dict[str, Any]:
        """Write once per `experiment_id`: the stored row. The same `launch` again is a replay
        (the stored row, its first `created_at`); another launch `IdempotencyConflict`."""

    async def experiments(self, provider_org_id: str) -> Sequence[dict[str, Any]]:
        """`{experiment_id, created_at, launch, report}` rows (report: B2's, or None)."""


class Catalog(Protocol):
    """What the provider can launch with (WR-LAB-API-2-1: D7 listings; R167 evaluators)."""

    async def catalog(self, provider_org_id: str) -> dict[str, Any]:
        """`{datasets, harnesses, servings, evaluators}` of the provider's own records."""

    async def evaluator(self, provider_org_id: str, evaluator_ref: str) -> dict[str, Any]:
        """The spec the ref's digest names; `NotFound` for any other."""


@dataclass(frozen=True)
class LabEvaluations:
    sessions: lab_auth.Sessions
    access: object                          # infrx.lab.access.LabAccess
    store: object | None = None             # D7: infrx.state.lab_data.PgLabDataStore
    experiments: ExperimentStore | None = None
    ledger: object | None = None            # B3's CheckpointLedger + `listing(provider)`
    catalog: Catalog | None = None

    def port(self, name: str):
        value = getattr(self, name)
        if value is None:                   # expected until its table merges: a 503
            raise errors.DependencyUnavailable(f"{name} is not wired")
        return value


# --- shared by the Lab routes of this lane ---------------------------------------------------
async def lab_actor(request: Request, sessions, access, capability: Cap) -> Actor:
    """The session's user and current membership of the named provider (`lab_auth`)."""
    user_id = await lab_auth.authenticate(request, sessions)
    membership = await lab_auth.member(
        access, user_id, request.query_params.get("provider_org_id", ""), capability)
    return Actor(provider_org_id=membership.provider_org_id, user_id=user_id,
                 role=membership.role)


async def lab_body(request: Request, rt, model, max_bytes: int = MAX_BODY_BYTES):
    """A JSON object body, bounded, validated by `model` (422 otherwise)."""
    intake.check_content_type(request)
    raw = await intake.read_body(request, max_bytes=max_bytes,
                                 timeout_s=rt.settings.pilot.intake_timeout_s, clock=rt.clock)
    try:
        return model.model_validate(intake.parse_object(intake.decode_utf8(raw)))
    except ValidationError:
        raise errors.InvalidRequest("invalid body") from None


# --- the operations -------------------------------------------------------------------------
def run_id(experiment_id: str, arm: str) -> str:
    """One D7 run per experiment and arm (B3's derivation)."""
    return checkpoints.run_id_of(experiment_id, arm)


def _run_payload(provider: str, launch: Launch, arm: str, created_at: str) -> dict[str, Any]:
    rid = run_id(launch.experiment_id, arm)
    return {"schema": "lab.eval_run.1", "provider_org_id": provider, "run_id": rid,
            "created_at": created_at, "dataset_ref": launch.dataset_ref,
            "harness_ref": launch.harness_ref,
            "serving_ref": getattr(launch, f"{arm}_serving_ref"),
            "evaluator_ref": launch.evaluator_ref, "seed": launch.seed, "environment": "dev",
            "max_cases": launch.max_cases, "state": "queued",
            "idempotency_key": lab.run_key(rid),
            "budgets": [{"limit": launch.run_limit.model_dump(),
                         "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]}


async def _status(store, rid: str, provider: str) -> dict[str, Any] | None:
    try:
        return await store.run_status(rid, provider_org_id=provider)
    except errors.NotFound:                 # not created: its freeze was refused
        return None


async def _experiment(store, provider: str, row: dict[str, Any]) -> dict[str, Any] | None:
    runs = [await _status(store, run_id(row["experiment_id"], arm), provider) for arm in ARMS]
    if None in runs:
        return None
    return {"experiment_id": row["experiment_id"], "created_at": row["created_at"],
            "protocol": row["launch"]["protocol"], "baseline": runs[0], "candidate": runs[1],
            "report": row["report"]}


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in PRIVATE}


async def launch(x: LabEvaluations, who: Actor, wanted: Launch) -> dict[str, Any]:
    spec = await x.port("catalog").evaluator(who.provider_org_id, wanted.evaluator_ref)
    store = x.port("store")
    now = await x.access.store.db_now()
    row = await x.port("experiments").put(who.provider_org_id, {
        "experiment_id": wanted.experiment_id, "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "launch": wanted.model_dump(mode="json", exclude_unset=True), "report": None})
    for arm in ARMS:
        await runner.freeze(store, _run_payload(who.provider_org_id, wanted, arm,
                                                row["created_at"]),
                            evaluator=spec, access=x.access, user_id=who.user_id,
                            provider_org_id=who.provider_org_id)
    return await _experiment(store, who.provider_org_id, row)


async def experiments(x: LabEvaluations, who: Actor) -> list[dict[str, Any]]:
    store = x.port("store")
    rows = [await _experiment(store, who.provider_org_id, row)
            for row in await x.port("experiments").experiments(who.provider_org_id)]
    return [row for row in rows if row is not None]


async def runs(x: LabEvaluations, who: Actor) -> list[dict[str, Any]]:
    store, provider = x.port("store"), who.provider_org_id
    ids = [run_id(row["experiment_id"], arm)
           for row in await x.port("experiments").experiments(provider) for arm in ARMS]
    ids += [d["run_id"] for sub in await x.port("ledger").listing(provider)
            for d in sub["decisions"] if d["run_id"]]
    found = [await _status(store, rid, provider) for rid in dict.fromkeys(ids)]
    return [run for run in found if run is not None]


async def cancel(x: LabEvaluations, who: Actor, rid: str) -> dict[str, Any]:
    store = x.port("store")
    status = await store.run_status(rid, provider_org_id=who.provider_org_id)
    if status["state"] not in LIVE:
        raise errors.StateConflict("the run already finished")
    # ponytail: read-then-cancel races a run finishing in between; lab-sql closes it by
    # refusing a terminal run in lab_cancel_run itself (WR-LAB-API-2-2).
    return await store.cancel_run(rid, provider_org_id=who.provider_org_id)


async def subscriptions(x: LabEvaluations, who: Actor) -> list[dict[str, Any]]:
    return [_public(row) for row in await x.port("ledger").listing(who.provider_org_id)]


async def subscribe(x: LabEvaluations, who: Actor, wanted: SubscriptionRequest) -> dict:
    spec = await x.port("catalog").evaluator(who.provider_org_id, wanted.evaluator_ref)
    ledger = x.port("ledger")
    asked = {**wanted.model_dump(mode="json"), "provider_org_id": who.provider_org_id,
             "evaluator": spec}
    stored = await checkpoints.subscribe(ledger, x.port("store"), asked, access=x.access,
                                         user_id=who.user_id)
    if stored.model_dump(mode="json", exclude={"owner_user_id"}) != asked:
        raise errors.IdempotencyConflict("the subscription id names another subscription")
    rows = await ledger.listing(who.provider_org_id)
    return _public(next(r for r in rows if r["subscription_id"] == wanted.subscription_id))


# --- the routes -----------------------------------------------------------------------------
def register(app, rt, evaluations: LabEvaluations | None = None):
    """Mount the evaluation routes over `evaluations` (default `rt.lab_evaluations`); without
    one nothing is mounted and `None` is returned."""
    x = evaluations if evaluations is not None else getattr(rt, "lab_evaluations", None)
    if x is None:
        return None

    async def actor(request: Request, capability: Cap) -> Actor:
        return await lab_actor(request, x.sessions, x.access, capability)

    def listing(read):
        @lab_auth.guarded
        async def handler(request: Request):
            who = await actor(request, Cap.read_aggregate_health)
            return lab_auth.ok({"data": await read(x, who)})
        return handler

    async def catalog(x, who):
        return await x.port("catalog").catalog(who.provider_org_id)

    for name, read in (("catalog", catalog), ("runs", runs), ("experiments", experiments),
                       ("subscriptions", subscriptions)):
        app.add_api_route(f"{EVALS_PREFIX}/{name}", listing(read), methods=["GET"])

    @app.post(EVALS_PREFIX + "/runs/{run_id}/cancel")
    @lab_auth.guarded
    async def cancel_run(request: Request):
        who = await actor(request, Cap.run_evaluation)
        return lab_auth.ok(await cancel(x, who, request.path_params["run_id"]))

    @app.post(f"{EVALS_PREFIX}/experiments")
    @lab_auth.guarded
    async def launch_experiment(request: Request):
        who = await actor(request, Cap.run_evaluation)
        return lab_auth.ok(await launch(x, who, await lab_body(request, rt, Launch)), 202)

    @app.post(f"{EVALS_PREFIX}/subscriptions")
    @lab_auth.guarded
    async def add_subscription(request: Request):
        who = await actor(request, Cap.run_evaluation)
        wanted = await lab_body(request, rt, SubscriptionRequest)
        return lab_auth.ok(await subscribe(x, who, wanted), 201)

    return x
