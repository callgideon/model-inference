"""WR-P4-1: `/lab/v1/pipelines`, the Lab's annotation and training surface over P1
(`infrx.pipelines.annotations`) and P3 (`infrx.pipelines.training`). Records are the Lab's
`apps/lab/lib/services/pipelines/port.ts` in snake_case; lists are `{"data": [...]}`.

    GET  labels?dataset_ref=  disagreements?dataset_ref=  label-imports  label-exports
         training-runs  training-runs/{id}/bundle  checkpoints  teacher-batches
    POST label-imports  assignments  reviews  adjudications  label-exports  training-runs
         training-runs/{id}/{submit|finish|cancel}  checkpoints  checkpoints/{id}/approve
         teacher-batches  teacher-batches/{id}/approve

(all under `/lab/v1/pipelines`, `?provider_org_id=`). As `/lab/v1/control` (`lab_auth`): the
actor is re-derived per call from the forwarded session and the current membership before a
body is read. Label values are content, so every call needs `run_evaluation` (a viewer reads
nothing); assigning a reviewer needs `manage_members`. P1/P3 re-check the reviewer, assignee
and adjudicator against the session user; the provider and user never come from a body.

* **The connector is never the caller's**: a body naming one is a 422 and every run is
  prepared and submitted with the manual bundle - the only one advertised until P-11.
* **Write-once ids** (the form's): a label import keeps one receipt per `import_id` (P1
  returns one but stores none), and a label export answers the export already made under its
  `export_id` - the same inputs again are the stored record, others a 409. A run's
  `external_run_id` and a checkpoint's id are P3's own write-once keys.
* **A teacher batch (P2) is a dry run until an administrator approves it**: `POST
  teacher-batches` stores the form's batch (write-once per `batch_id`) and answers the plan -
  each chunk's reservation at the rate in force, the cost ceiling against the batch's USD budget
  and its named payer - with nothing reserved or sent. `approve` is addressed to the batch
  (R183: 404, then `manage_members`), refuses an unpriced or over-budget ceiling (409) and
  live submission off (503), records the approval, then runs P2's `run_batch` (J2's egress to
  the local teacher fake until P-10); a second approval resumes and sends nothing twice. Each
  chunk's state (an ambiguous one included), hold, cost and per-item failures are read back
  from P2's ledger (D8's `PgTeacherLedger`); `unreserved` is a chunk never reserved.
* An expired export is `410 gone`. Nothing here settles, estimates or converts a cost: a
  run's cost is the provider-reported PROVIDER_USD or null (unknown).

A port without its table yet answers 503: the label log (D8, SR-P1-1), the run ledger and its
listings (SR-P3-1; WR-LAB2-4), the B3 evaluation port P3 uses for checkpoints. Mounted
only when the composition put a `LabPipelines` on `rt.lab_pipelines` (LAB_PIPELINES, off).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, Sequence

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import Field

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import ProviderCapability as Cap
from ...datasets.imports import write_once
from ...contracts.v2.money_units import ProviderUsd
from ...datasets.versions import MAX_EXPORT_TTL_S
from ...judge.cost import JUDGE_MODE_LIVE, worst_case
from ...judge.dryrun import MAX_CANDIDATES
from ...judge.submit import require_own_payer
from ...pipelines import annotations as p1
from ...pipelines import teachers as p2
from ...pipelines import training as p3
from .. import lab_auth
from .lab_evaluations import held, lab_actor, lab_body, require

PIPELINES_PREFIX = "/lab/v1/pipelines"
MAX_BODY_BYTES = 4 * 2**20                  # a label import's rows (the Lab caps them at 1 MB)
USD = r"^(0|[1-9][0-9]{0,11})\.[0-9]{8}$"   # a Lab Amount value (R159), PROVIDER_USD
ZERO = "0.00000000"


class Body(lab.LabModel):
    pass


class ImportBody(Body):
    import_id: lab.Uuid
    dataset_ref: lab.RefOf("dataset")
    rubric_ref: lab.RefOf("rubric")
    rows: str = Field(max_length=MAX_BODY_BYTES)      # JSON lines, one label row each


class AssignBody(Body):
    dataset_ref: lab.RefOf("dataset")
    sample_id: lab.Uuid
    reviewer_id: lab.Uuid
    rubric_ref: lab.RefOf("rubric")


class ReviewBody(Body):
    dataset_ref: lab.RefOf("dataset")
    annotation_ref: lab.RefOf("annotation")
    decision: Literal["accepted", "rejected"]
    rubric_ref: lab.RefOf("rubric")
    correction: str | None                  # the reviewer's label value, JSON text


class AdjudicateBody(Body):
    dataset_ref: lab.RefOf("dataset")
    sample_id: lab.Uuid
    value: str                              # JSON text
    rubric_ref: lab.RefOf("rubric")


class ExportBody(Body):
    export_id: lab.Uuid
    dataset_ref: lab.RefOf("dataset")
    adapter: Literal[p1.ADAPTERS]
    ttl_s: int = Field(ge=1, le=MAX_EXPORT_TTL_S)


class ExportPin(Body):
    format: Literal[tuple(p3.EXPORTS)]
    export_id: lab.Uuid


class Config(Body):
    objective: Literal["sft", "preference"]
    adaptation: Literal["full", "lora"]
    base_model: str = Field(min_length=1, max_length=128)


class PrepareBody(Body):
    """No `connector`: a body naming one is refused (P-11)."""

    external_run_id: lab.Uuid
    dataset_ref: lab.RefOf("dataset")
    export: ExportPin
    config: Config
    payer_ref: lab.RefOf("payer")
    limit: str = Field(pattern=USD)


class CheckpointBody(Body):
    external_run_id: lab.Uuid
    checkpoint_id: lab.Uuid
    artifact_key: str = Field(min_length=1, max_length=512)
    artifact_digest: lab.Sha256


class ApproveBody(Body):
    external_run_id: lab.Uuid


class TeacherBody(Body):
    """A P2 batch's dry run. The batch id is the form's (write-once); nothing is sent."""

    batch_id: lab.Uuid
    dataset_ref: lab.RefOf("dataset")
    rubric_ref: lab.RefOf("rubric")
    teacher_model: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    payer_ref: lab.RefOf("payer")
    budget_usd: str = Field(pattern=USD)
    chunk_size: int = Field(ge=1, le=MAX_CANDIDATES)


class RunListing(Protocol):
    """P3's `RunLedger` plus the listings WR-LAB2-4 asks of lab-sql (SR-P3-1)."""

    async def run_rows(self, provider_org_id: str) -> Sequence[tuple[str, dict[str, Any]]]:
        """(external_run_id, the ledger row) of every run of the provider."""

    async def checkpoint_rows(self, provider_org_id: str) -> Sequence[dict[str, Any]]:
        """Every checkpoint receipt (D7) of the provider with P3's outcome note:
        `{checkpoint_id, external_run_ref, artifact_digest, state, reason}`."""


@dataclass(frozen=True)
class LabPipelines:
    sessions: lab_auth.Sessions
    access: object                          # infrx.lab.access.LabAccess (its store: members)
    store: object | None = None             # D7: infrx.state.lab_data.PgLabDataStore
    objects: object | None = None           # the Lab object store (bundles, exports, receipts)
    log: object | None = None               # D8: P1's LabelLog
    ledger: object | None = None            # D8/D6J: P3's RunLedger + RunListing
    evals: object | None = None             # B3: P3's Evaluations
    teachers: object | None = None          # P2's TeacherWiring (ledger: D8 PgTeacherLedger)

    def port(self, name: str):
        value = getattr(self, name)
        if value is None:                   # expected until its table merges: a 503
            raise errors.DependencyUnavailable(f"{name} is not wired")
        return value


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        raise errors.InvalidRequest("a label value is JSON") from None


def _rows(text: str) -> list[Any]:
    """One row per non-empty line; an unreadable line is P1's `missing_evidence` row."""
    out = []
    for line in text.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append(None)
    return out


def _digest(value: Any) -> str:
    return hashlib.sha256(lab.canonical(value)).hexdigest()


async def _now(x: LabPipelines) -> Any:
    return await x.access.store.db_now()


# --- P1: labels -----------------------------------------------------------------------------
async def labels(x: LabPipelines, who, dataset_ref: str) -> list[dict[str, Any]]:
    store, provider = x.port("store"), who.provider_org_id
    # ponytail: P1's lineage-aware read is private; ask P1 for a public `labels()` if it moves
    _, found = await p1._labels(store, x.port("log"), provider, dataset_ref)
    out = []
    for ref, (_, sample, state) in found.items():
        record = await store.resolve(ref, provider_org_id=provider)
        out.append({"annotation_ref": ref, "sample_id": sample, "method": record.method,
                    "ground_truth": record.ground_truth, "state": state,
                    "value": lab.canonical(record.label["value"]).decode(),
                    "reviewer_id": record.reviewer_id})
    return out


async def disagreements(x: LabPipelines, who, dataset_ref: str) -> list[dict[str, Any]]:
    found = await p1.disagreements(x.port("store"), x.port("log"),
                                   provider_org_id=who.provider_org_id, dataset_ref=dataset_ref)
    return [{"sample_id": s, "annotation_refs": refs} for s, refs in sorted(found.items())]


def _receipt_key(provider: str, import_id: str) -> str:
    return f"lab/{provider}/label-imports/{import_id}/receipt.json"


def _receipt(stored: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in stored.items() if k != "request_sha256"}


async def import_labels(x: LabPipelines, who, body: ImportBody) -> dict[str, Any]:
    objects, provider = x.port("objects"), who.provider_org_id
    key = _receipt_key(provider, body.import_id)
    asked = _digest(body.model_dump(mode="json"))
    stored = await objects.get(key)
    if stored is None:
        done = await p1.import_labels(x.port("store"), x.port("log"), objects=objects,
                                      now=await _now(x), provider_org_id=provider,
                                      actor=who.user_id, dataset_ref=body.dataset_ref,
                                      rubric_ref=body.rubric_ref, rows=_rows(body.rows))
        receipt = {"import_id": body.import_id, "dataset_ref": body.dataset_ref,
                   "accepted": len(done.accepted), "rejected": done.rejected,
                   "request_sha256": asked}
        await write_once(objects, key, lab.canonical(receipt))
        stored = await objects.get(key)
    receipt = json.loads(stored)
    if receipt["request_sha256"] != asked:
        raise errors.IdempotencyConflict("the import id names another import")
    return _receipt(receipt)


async def imports(x: LabPipelines, who) -> list[dict[str, Any]]:
    objects = x.port("objects")
    keys = await objects.keys(f"lab/{who.provider_org_id}/label-imports/")
    return [_receipt(json.loads(await objects.get(key))) for key in sorted(keys)]


async def assign(x: LabPipelines, who, body: AssignBody) -> dict[str, Any]:
    await p1.assign(x.port("log"), x.access.store, provider_org_id=who.provider_org_id,
                    user_id=who.user_id, dataset_ref=body.dataset_ref,
                    sample_id=body.sample_id, reviewer_id=body.reviewer_id,
                    rubric_ref=body.rubric_ref)
    return {}


async def review(x: LabPipelines, who, body: ReviewBody) -> dict[str, Any]:
    correction = None if body.correction is None else _json(body.correction)
    ref = await p1.review(x.port("store"), x.port("log"), x.access.store,
                          provider_org_id=who.provider_org_id, user_id=who.user_id,
                          dataset_ref=body.dataset_ref, annotation_ref=body.annotation_ref,
                          decision=body.decision, rubric_ref=body.rubric_ref,
                          correction=correction)
    return {"annotation_ref": ref}


async def adjudicate(x: LabPipelines, who, body: AdjudicateBody) -> dict[str, Any]:
    ref = await p1.adjudicate(x.port("store"), x.port("log"), x.access.store,
                              provider_org_id=who.provider_org_id, user_id=who.user_id,
                              dataset_ref=body.dataset_ref, sample_id=body.sample_id,
                              value=_json(body.value), rubric_ref=body.rubric_ref)
    return {"annotation_ref": ref}


def _export_key(provider: str, export_id: str) -> str:
    return f"lab/{provider}/label-exports/{export_id}/export.json"


async def export_labels(x: LabPipelines, who, body: ExportBody) -> dict[str, Any]:
    objects, provider = x.port("objects"), who.provider_org_id
    stored = await objects.get(_export_key(provider, body.export_id))
    if stored is None:
        return await p1.export(x.port("store"), x.port("log"), objects,
                               provider_org_id=provider, dataset_ref=body.dataset_ref,
                               export_id=body.export_id, adapter=body.adapter,
                               now=await _now(x), ttl_s=body.ttl_s)
    record = json.loads(stored)
    ttl = datetime.fromisoformat(record["expires_at"]) - datetime.fromisoformat(
        record["created_at"])
    if (record["dataset_ref"], record["adapter"], ttl.total_seconds()) != (
            body.dataset_ref, body.adapter, body.ttl_s):
        raise errors.IdempotencyConflict("the export id names another export")
    return record


async def exports(x: LabPipelines, who) -> list[dict[str, Any]]:
    objects = x.port("objects")
    keys = await objects.keys(f"lab/{who.provider_org_id}/label-exports/")
    return [json.loads(await objects.get(key)) for key in sorted(keys)
            if key.endswith("/export.json")]


# --- P3: training ---------------------------------------------------------------------------
async def _bundle(x: LabPipelines, provider: str, external_run_id: str) -> dict[str, Any]:
    return await p3._bundle(x.port("objects"), provider, external_run_id)


async def _training_run(x: LabPipelines, provider: str, external_run_id: str,
                        row: dict[str, Any]) -> dict[str, Any]:
    bundle = await _bundle(x, provider, external_run_id)
    config = bundle["config"]
    return {"external_run_id": external_run_id, "run_ref": row["run_ref"],
            "connector": row["connector"], "state": row["state"],
            "dataset_ref": bundle["dataset_ref"], "export": bundle["export"],
            "config": {k: config[k] for k in ("objective", "adaptation", "base_model")},
            "train": len(bundle["train"]), "dev": len(bundle["dev"]),
            "omitted": len(bundle["omitted"]), "holdout": bundle["holdout"],
            "payer_ref": row["payer_ref"], "limit_usd": row["limit"],
            # ponytail: the manual bundle reserves nothing; read D6J's reservation once an
            # automatic connector is advertised (P-11)
            "reserved_usd": ZERO, "settled": "cost" in row, "cost_usd": row.get("cost"),
            "reason": row.get("reason")}


async def _answer(x: LabPipelines, provider: str, external_run_id: str) -> dict[str, Any]:
    row = await p3._run(x.port("ledger"), provider, external_run_id)
    return await _training_run(x, provider, external_run_id, row)


async def training_runs(x: LabPipelines, who) -> list[dict[str, Any]]:
    provider = who.provider_org_id
    return [await _training_run(x, provider, rid, row)
            for rid, row in await x.port("ledger").run_rows(provider)]


async def bundle(x: LabPipelines, who, external_run_id: str) -> dict[str, Any]:
    await p3._run(x.port("ledger"), who.provider_org_id, external_run_id)
    return await _bundle(x, who.provider_org_id, external_run_id)


async def prepare(x: LabPipelines, who, body: PrepareBody) -> dict[str, Any]:
    provider = who.provider_org_id
    await p3.prepare(x.port("store"), x.port("objects"), x.port("ledger"),
                     provider_org_id=provider, actor=who.user_id,
                     external_run_id=body.external_run_id, dataset_ref=body.dataset_ref,
                     config={**body.config.model_dump(), "environment": {}},
                     export=body.export.model_dump(), payer_ref=body.payer_ref,
                     limit=body.limit, now=await _now(x), connector=p3.MANUAL)
    return await _answer(x, provider, body.external_run_id)


async def move(x: LabPipelines, who, external_run_id: str, op: str) -> dict[str, Any]:
    provider, ledger, manual = who.provider_org_id, x.port("ledger"), p3.ManualConnector()
    if op == "submit":
        await p3.submit(x.port("store"), x.port("objects"), ledger, manual, x.access.store,
                        provider_org_id=provider, user_id=who.user_id,
                        external_run_id=external_run_id)
    elif op == "finish":
        await p3.finish(ledger, x.access.store, provider_org_id=provider,
                        user_id=who.user_id, external_run_id=external_run_id)
    else:
        await p3.cancel(ledger, manual, provider_org_id=provider,
                        external_run_id=external_run_id)
    return await _answer(x, provider, external_run_id)


async def _checkpoint(x: LabPipelines, provider: str, row: dict[str, Any]) -> dict[str, Any]:
    cid = row["checkpoint_id"]
    evaluation = await x.port("evals").evaluation(provider_org_id=provider, checkpoint_id=cid)
    eligible = await x.port("ledger").noted(f"eligible:{cid}", provider_org_id=provider)
    return {"checkpoint_id": cid,
            "external_run_id": lab.REF_RE.fullmatch(row["external_run_ref"]).group(3),
            "artifact_digest": row["artifact_digest"], "state": row["state"],
            "reason": row.get("reason"),
            "evaluation": None if evaluation is None else {
                k: evaluation[k] for k in ("run_ref", "state", "split", "holdout_sha256")},
            "eligible": eligible is not None}


async def checkpoints(x: LabPipelines, who) -> list[dict[str, Any]]:
    provider = who.provider_org_id
    return [await _checkpoint(x, provider, row)
            for row in await x.port("ledger").checkpoint_rows(provider)]


async def _one_checkpoint(x: LabPipelines, provider: str, checkpoint_id: str) -> dict:
    rows = await x.port("ledger").checkpoint_rows(provider)
    row = next((r for r in rows if r["checkpoint_id"] == checkpoint_id), None)
    if row is None:
        raise errors.NotFound("no such checkpoint")
    return await _checkpoint(x, provider, row)


async def import_checkpoint(x: LabPipelines, who, body: CheckpointBody) -> dict[str, Any]:
    await p3.import_checkpoint(x.port("store"), x.port("objects"), x.port("ledger"),
                               x.port("evals"), provider_org_id=who.provider_org_id,
                               external_run_id=body.external_run_id,
                               checkpoint_id=body.checkpoint_id,
                               artifact_key=body.artifact_key,
                               artifact_digest=body.artifact_digest)
    return await _one_checkpoint(x, who.provider_org_id, body.checkpoint_id)


async def approve(x: LabPipelines, who, checkpoint_id: str, body: ApproveBody) -> dict:
    await p3.approve(x.port("objects"), x.port("ledger"), x.port("evals"), x.access.store,
                     provider_org_id=who.provider_org_id, user_id=who.user_id,
                     external_run_id=body.external_run_id, checkpoint_id=checkpoint_id)
    return await _one_checkpoint(x, who.provider_org_id, checkpoint_id)


# --- P2: teacher batches -------------------------------------------------------------------
def _batch_key(provider: str, batch_id: str, name: str = "batch.json") -> str:
    return f"lab/{provider}/teacher-batches/{batch_id}/{name}"


def _teacher(stored: dict[str, Any], provider: str, user_id: str) -> p2.TeacherBatch:
    return p2.TeacherBatch(
        batch_id=stored["batch_id"], provider_org_id=provider, requested_by=user_id,
        dataset_ref=stored["dataset_ref"], rubric_ref=stored["rubric_ref"],
        teacher_model=stored["teacher_model"], prompt_version=stored["prompt_version"],
        payer_ref=stored["payer_ref"], chunk_size=stored["chunk_size"])


def _usd(amount) -> str | None:
    return None if amount is None else str(amount)


async def _teacher_batch(x: LabPipelines, provider: str, stored: dict) -> dict[str, Any]:
    """The stored batch, its plan as of now and each chunk as P2's ledger records it."""
    wiring, batch = x.port("teachers"), _teacher(stored, provider, stored["requested_by"])
    now = await wiring.ledger.db_now()
    planned = await p2.plan(batch, store=wiring.store, objects=wiring.objects, rates=wiring.rates, now=now)
    rate = wiring.rates.rate_for(batch.teacher_model, now)
    chunks = []
    for run_id, ids in planned.chunks:
        run = await wiring.ledger.run(run_id)
        chunks.append({
            "run_id": run_id, "samples": len(ids),
            "ceiling_usd": rate and str(ProviderUsd(worst_case(rate, batch.ceilings, len(ids)))),
            "state": "unreserved" if run is None else run.state,
            "reserved_usd": run and str(run.reserved), "cost_usd": run and _usd(run.actual),
            "sent": 0 if run is None else len(run.sent_ids),
            "failures": [] if run is None else [{"sample_id": s, "reason": r}
                                                for s, r in await wiring.ledger.failures(run_id)]})
    approval = await x.port("objects").get(_batch_key(provider, batch.batch_id, "approval.json"))
    ceiling = planned.worst_case
    return {**_receipt(stored), "price_version": planned.price_version,
            "ceiling_usd": _usd(ceiling),
            "within_budget": ceiling is not None and ceiling <= ProviderUsd(stored["budget_usd"]),
            "holdout": len(planned.omitted), "not_permitted": len(planned.not_permitted),
            "approval": approval and json.loads(approval), "chunks": chunks}


async def teacher_batches(x: LabPipelines, who) -> list[dict[str, Any]]:
    objects, provider = x.port("objects"), who.provider_org_id
    x.port("teachers")
    keys = await objects.keys(f"lab/{provider}/teacher-batches/")
    return [await _teacher_batch(x, provider, json.loads(await objects.get(key)))
            for key in sorted(keys) if key.endswith("/batch.json")]


async def plan_teachers(x: LabPipelines, who, body: TeacherBody) -> dict[str, Any]:
    """The dry run: stored once per batch id, planned, nothing reserved or sent."""
    objects, wiring, provider = x.port("objects"), x.port("teachers"), who.provider_org_id
    try:
        require_own_payer(provider, body.payer_ref)
    except errors.Forbidden:
        raise errors.InvalidRequest("the payer is not this provider's") from None
    key, asked = _batch_key(provider, body.batch_id), _digest(body.model_dump(mode="json"))
    if await objects.get(key) is None:
        await held(wiring.store.resolve(body.dataset_ref, provider_org_id=provider))
        await write_once(objects, key, lab.canonical({
            **body.model_dump(mode="json"), "requested_by": who.user_id,
            "request_sha256": asked}))
    stored = json.loads(await objects.get(key))
    if stored["request_sha256"] != asked:
        raise errors.IdempotencyConflict("the batch id names another batch")
    return await _teacher_batch(x, provider, stored)


async def approve_teachers(x: LabPipelines, who, batch_id: str) -> dict[str, Any]:
    """The administrator's live submit, within the batch's budget; a resume sends nothing
    twice (P2's run ids and J2's one submit intent per run)."""
    objects, provider = x.port("objects"), who.provider_org_id
    found = await objects.get(_batch_key(provider, batch_id))
    if found is None:
        raise errors.NotFound("no such teacher batch")
    require(who, Cap.manage_members)
    wiring, stored = x.port("teachers"), json.loads(found)
    batch = _teacher(stored, provider, who.user_id)
    now = await wiring.ledger.db_now()
    ceiling = (await p2.plan(batch, store=wiring.store, objects=wiring.objects, rates=wiring.rates, now=now)).worst_case
    if ceiling is None or ceiling > ProviderUsd(stored["budget_usd"]):
        raise errors.StateConflict("the batch's cost ceiling is unpriced or over its budget")
    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:
        raise errors.DependencyUnavailable("live teacher submission is off")
    await objects.put_if_absent(_batch_key(provider, batch_id, "approval.json"), lab.canonical({
        "approved_by": who.user_id, "approved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}),
        "application/json")
    await p2.run_batch(batch, wiring=wiring)
    return await _teacher_batch(x, provider, stored)


# --- the routes -----------------------------------------------------------------------------
def _guarded(handler):
    """`lab_auth.guarded`, plus P1/P3's `Gone` (an expired export) as the port's `gone`."""
    async def wrapped(request: Request):
        try:
            return await handler(request)
        except errors.Gone:
            return JSONResponse({"refusal": "gone"}, status_code=410, headers=lab_auth.NO_STORE)
    return lab_auth.guarded(wrapped)


def register(app, rt, pipelines: LabPipelines | None = None):
    """Mount the pipeline routes over `pipelines` (default `rt.lab_pipelines`); without one
    nothing is mounted and `None` is returned."""
    x = pipelines if pipelines is not None else getattr(rt, "lab_pipelines", None)
    if x is None:
        return None
    P = PIPELINES_PREFIX

    async def actor(request: Request, capability: Cap):
        return await lab_actor(request, x.sessions, x.access, capability)

    def route(method: str, path: str, answer, *, model=None, status: int = 200,
              capability: Cap = Cap.run_evaluation, listing: bool = False, query=()):
        """`answer(x, who, *path params, *query params, body)`."""
        @_guarded
        async def handler(request: Request):
            who = await actor(request, capability)
            args = [*request.path_params.values(),
                    *(request.query_params.get(name, "") for name in query)]
            if model is not None:
                args.append(await lab_body(request, rt, model, MAX_BODY_BYTES))
            result = await answer(x, who, *args)
            return lab_auth.ok({"data": result} if listing else result, status)
        app.add_api_route(P + path, handler, methods=[method])

    for path, read in (("/labels", labels), ("/disagreements", disagreements)):
        route("GET", path, read, listing=True, query=("dataset_ref",))
    for path, read in (("/label-imports", imports), ("/label-exports", exports),
                       ("/training-runs", training_runs), ("/checkpoints", checkpoints),
                       ("/teacher-batches", teacher_batches)):
        route("GET", path, read, listing=True)
    route("GET", "/training-runs/{external_run_id}/bundle", bundle)
    route("POST", "/label-imports", import_labels, model=ImportBody, status=201)
    route("POST", "/assignments", assign, model=AssignBody, capability=Cap.manage_members)
    route("POST", "/reviews", review, model=ReviewBody)
    route("POST", "/adjudications", adjudicate, model=AdjudicateBody)
    route("POST", "/label-exports", export_labels, model=ExportBody, status=201)
    route("POST", "/training-runs", prepare, model=PrepareBody, status=201)
    for op in ("submit", "finish", "cancel"):
        route("POST", "/training-runs/{external_run_id}/" + op,
              lambda x, who, rid, op=op: move(x, who, rid, op))
    route("POST", "/checkpoints", import_checkpoint, model=CheckpointBody, status=201)
    route("POST", "/checkpoints/{checkpoint_id}/approve", approve, model=ApproveBody)
    route("POST", "/teacher-batches", plan_teachers, model=TeacherBody, status=201)
    route("POST", "/teacher-batches/{batch_id}/approve", approve_teachers,
          capability=Cap.read_aggregate_health)       # R183: the batch first, then the role
    return x
