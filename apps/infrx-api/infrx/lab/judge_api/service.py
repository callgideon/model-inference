"""AP-08 08a: the typed judge and review API over the session doors (`doors.py`).

Every write's identity comes from the caller's `Idempotency-Key`, scoped to the action, the
provider workspace and the session user (`scoped_id`), and is made durable by the door itself:
a run id (0037's `request_run` is idempotent on it), a config id and a review id (0064, the
stored values compared), a budget key digest (0064, recorded in the limit version). The same
key and body answer the stored outcome; the same key with another body is 409. Nothing is kept
in process. Money is R270 `Money` (PROVIDER_USD, exact decimal text); an estimate is a report
from the approved rate table and never a reservation.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime, UTC
from typing import Any, Literal
from collections.abc import Callable

from pydantic import Field

from ...contracts import api, errors
from ...contracts.api import Money, OperationDoc, Wire
from ...judge.cost import APPROVED_RATES, RateTable, estimate_worst_case
from ...judge import rubric as j
from ...judge.dryrun import DEFAULT_CEILINGS
from . import rubric
from .doors import SessionDoors

UUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
#: Fixed forever: the namespace every scoped id of this API is derived in.
NAMESPACE = uuid.UUID("0b8e3d7a-58a1-5a8e-9f0e-000000000a08")
MAX_KEY_CHARS = 255

DomainState = Literal["queued", "reserved", "submitting", "submitted", "ambiguous", "completed",
                      "failed", "cancelled"]
_LEDGER: dict[str, DomainState] = {"prepared": "reserved", "submitting": "submitting", "submitted": "submitted",
           "ambiguous": "ambiguous", "completed": "completed", "failed": "failed",
           "cancelled": "cancelled"}
_OPERATION: dict[DomainState, api.OperationState] = {"queued": "queued", "reserved": "running", "submitting": "running",
              "submitted": "running", "ambiguous": "running", "completed": "succeeded",
              "failed": "failed", "cancelled": "cancelled"}


# ------------------------------------------------------------------------------- wire
class ConfigBody(Wire):
    grantor_org_id: str = Field(pattern=UUID)
    model_id: str = Field(pattern=UUID)
    judge_model: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    rubric_version: int = Field(ge=1, le=1000)
    sample_size: int = Field(ge=1, le=200)


class ConfigDoc(Wire):
    config_id: str
    grantor_org_id: str
    model_id: str
    judge_model: str
    rubric_version: int
    sample_size: int
    created_at: str
    calibration: rubric.CalibrationDoc


class BudgetBody(Wire):
    limit: Money


class BudgetDoc(Wire):
    payer_ref: str
    limit: Money
    reserved: Money
    settled: Money
    version: int | None = None


class EstimateBody(Wire):
    config_id: str = Field(pattern=UUID)


class EstimateDoc(Wire):
    """A report: the worst case a live run of this configuration would reserve. `eligible`
    is null - counting eligible traces needs the trace store, which this API does not read."""

    config_id: str
    judge_model: str
    samples_max: int
    priced: bool
    price_version: str | None = None
    per_sample: Money | None = None
    worst_case: Money | None = None
    eligible: int | None = None
    authorizes_spend: Literal[False] = False
    note: str


class RunBody(Wire):
    config_id: str = Field(pattern=UUID)
    payer_ref: str = Field(min_length=1, max_length=400)


class RunDoc(Wire):
    run_id: str
    config_id: str
    payer_ref: str
    grantor_org_id: str
    model_id: str
    judge_model: str
    rubric_version: int
    sample_size: int
    domain_state: DomainState
    cancel_requested: bool
    selected: int
    sent: int
    media: int
    accepted: int
    rejected: int
    reserved: Money | None = None
    settled: Money | None = None
    price_version: str | None = None
    requested_at: str
    operation: OperationDoc


class CalibrationView(rubric.CalibrationDoc):
    config_id: str


class Signal(Wire):
    """A stored customer (or judge) signal shared with the provider (0038)."""

    feedback_id: str
    name: str
    value: bool | int | str
    comment: str | None = None
    author_role: Literal["customer", "judge"]
    channel: str
    created_at: str


class ReviewBody(Wire):
    verdict: Literal["pass", "fail", "unsure"]
    comment: str | None = Field(default=None, min_length=1, max_length=2000)
    run_id: str | None = Field(default=None, pattern=UUID)
    rubric_version: int | None = Field(default=None, ge=1, le=1000)


class ReviewDoc(Wire):
    review_id: str
    request_id: str
    reviewer: str
    provenance: Literal["human"]
    verdict: Literal["pass", "fail", "unsure"]
    comment: str | None = None
    run_id: str | None = None
    rubric_version: int | None = None
    created_at: str


class FeedbackDoc(Wire):
    """The customer's shared signals and the provider's human reviews, kept apart: a review's
    provenance is `human`, a signal's its author role; neither is a judge result."""

    signals: tuple[Signal, ...]
    reviews: tuple[ReviewDoc, ...]


class JudgeModelDoc(Wire):
    model: str
    price_version: str
    input_per_million: Money
    output_per_million: Money
    source: str
    effective_at: str


class JudgeModels(Wire):
    data: tuple[JudgeModelDoc, ...]
    next_cursor: str | None = None
    availability: api.Availability


# ----------------------------------------------------------------------------- helpers
def _money(amount: Any) -> Money:
    return Money(amount=str(amount), unit="PROVIDER_USD")


def _usd(amount: Any) -> Money | None:
    return None if amount is None else _money(amount)


def scoped_id(action: str, provider: str, user: str, key: str, *more: str) -> uuid.UUID:
    """The durable identity of one keyed write: the key scoped to action, workspace, actor."""
    if not 0 < len(key) <= MAX_KEY_CHARS or not key.isascii() or not key.isprintable():
        raise errors.InvalidRequest("Idempotency-Key is 1..255 printable ASCII characters",
                                    param="Idempotency-Key")
    return uuid.uuid5(NAMESPACE, "\n".join((action, provider, user, key, *more)))


def input_hash(body: Wire, *more: str) -> str:
    canonical = json.dumps([body.model_dump(mode="json"), *more], sort_keys=True,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def encode_cursor(last: str) -> str:
    # ponytail: opaque but unsigned; the door scopes every keyset to the session's provider,
    # so a forged cursor only moves within what the caller may read. AP-02's HMAC cursor
    # (actor|operation|filters) replaces it when composed.
    return base64.urlsafe_b64encode(last.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        last = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        return str(uuid.UUID(last))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise errors.InvalidCursor("this cursor was not issued here") from None


def page(rows: list[Any], limit: int, key: Callable[[Any], str]) -> tuple[list[Any], str | None]:
    """`limit + 1` rows asked for; the extra one only says another page exists."""
    if len(rows) > limit:
        return rows[:limit], encode_cursor(key(rows[limit - 1]))
    return rows, None


def domain_state(row: dict[str, Any]) -> DomainState:
    """The ledger's state (0036) when the worker reserved one; else `queued` - or `cancelled`
    once a cancel was recorded, because the start step never starts a cancelled request."""
    ledger = row.get("ledger_state")
    if ledger is None:
        return "cancelled" if row.get("cancel_requested_at") else "queued"
    return _LEDGER[ledger]


def run_doc(row: dict[str, Any]) -> RunDoc:
    state = domain_state(row)
    cancel = row.get("cancel_requested_at") is not None
    op_state: api.OperationState = _OPERATION[state]
    if cancel and op_state == "running":
        op_state = "cancel_requested"
    return RunDoc(
        run_id=row["run_id"], config_id=row["config_id"], payer_ref=row["payer_ref"],
        grantor_org_id=row["grantor_org_id"], model_id=row["model_id"],
        judge_model=row["judge_model"], rubric_version=row["rubric_version"],
        sample_size=row["sample_size"], domain_state=state, cancel_requested=cancel,
        selected=row["selected"], sent=row["sent"], media=row["media"],
        accepted=row["accepted"], rejected=row["rejected"], reserved=_usd(row["reserved"]),
        settled=_usd(row["actual"]), price_version=row["price_version"],
        requested_at=str(row["requested_at"]),
        operation=OperationDoc(operation_id=row["run_id"], kind="judge.run", state=op_state,
                               phase=state, resource_id=row["run_id"],
                               created_at=str(row["requested_at"]),
                               updated_at=str(row["updated_at"]),
                               retry_after_s=None if op_state in api.TERMINAL_STATES else 30))


def config_doc(row: dict[str, Any]) -> ConfigDoc:
    return ConfigDoc(config_id=row["config_id"], grantor_org_id=row["grantor_org_id"],
                     model_id=row["model_id"], judge_model=row["judge_model"],
                     rubric_version=row["rubric_version"], sample_size=row["sample_size"],
                     created_at=str(row["created_at"]),
                     calibration=rubric.calibration(row["calibration"]))


def budget_doc(row: dict[str, Any]) -> BudgetDoc:
    return BudgetDoc(payer_ref=row["payer_ref"], limit=_money(row["limit"]),
                     reserved=_money(row["reserved"]), settled=_money(row["settled"]),
                     version=row.get("version"))


def _uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise errors.NotFound(f"no such {what}") from None


# ----------------------------------------------------------------------------- the API
class JudgeApi:
    """The judge and review operations, each as the session user `user` of `provider`."""

    def __init__(self, doors: SessionDoors, *, rates: RateTable = APPROVED_RATES,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.doors, self.rates, self.clock = doors, rates, clock

    def models(self) -> JudgeModels:
        rows = tuple(getattr(self.rates, "rows", ()))
        data = tuple(JudgeModelDoc(model=r.model, price_version=r.price_version,
                                   input_per_million=_money(r.input_per_million),
                                   output_per_million=_money(r.output_per_million),
                                   source=r.source, effective_at=r.effective_at.isoformat())
                     for r in rows)
        availability = api.Availability(state="configured") if data else api.Availability(
            state="unavailable", reason="no approved judge rate (P-10)")
        return JudgeModels(data=data, availability=availability)

    async def stored_rubrics(self, user: str, provider: str) -> dict[int, dict[str, Any]]:
        """SR-AP08-1's stored versions, by version."""
        rows = await self.doors.call(user, "lab_judge_rubric_list", _uuid(provider, "provider"))
        return {row["rubric_version"]: row for row in rows}

    async def rubrics(self, user: str, provider: str) -> api.ListPage[rubric.RubricDoc]:
        return api.ListPage[rubric.RubricDoc](
            data=rubric.rubrics(await self.stored_rubrics(user, provider)))

    async def create_rubric(self, user: str, provider: str, key: str,
                            body: rubric.RubricBody) -> rubric.RubricDoc:
        """A reviewed definition -> a new immutable version (the door: operator only, once
        per version; the same digest again is the stored row, another one 409)."""
        scoped_id("judge.rubric", provider, user, key)    # validated; the version is the identity
        try:
            r = j.from_definition(body.definition())
        except ValueError as refused:
            raise errors.InvalidRequest(str(refused), param="criteria") from None
        if r.version in rubric.RUBRICS:
            raise errors.StateConflict(f"rubric version {r.version} is a reviewed code version "
                                       f"and immutable")
        row = await self.doors.call(user, "lab_judge_rubric_create", {
            "rubric_version": r.version, "rubric_id": r.rubric_id,
            "definition": j.definition(r), "digest": j.digest(r),
            "review_ref": body.review_ref})
        return rubric.stored_doc(row)

    async def configure(self, user: str, provider: str, key: str, body: ConfigBody) -> ConfigDoc:
        version = body.rubric_version
        if version not in rubric.RUBRICS and \
                version not in await self.stored_rubrics(user, provider):
            if version in j.PENDING:
                raise errors.StateConflict(f"rubric version {version} is definition_pending: "
                                           f"{j.PENDING[version][1]}")
            raise errors.InvalidRequest("no such rubric version", param="rubric_version")
        config = scoped_id("judge.config", provider, user, key)
        row = await self.doors.call(user, "lab_judge_configure_keyed", _uuid(provider, "provider"),
                                    str(config), body.grantor_org_id, body.model_id,
                                    body.judge_model, body.rubric_version, body.sample_size)
        return config_doc(row)

    async def configs(self, user: str, provider: str, cursor: str | None,
                      limit: int) -> api.ListPage[ConfigDoc]:
        rows = await self.doors.call(user, "lab_judge_config_list", _uuid(provider, "provider"),
                                     None, decode_cursor(cursor), limit + 1)
        shown, nxt = page(rows, limit, lambda r: r["config_id"])
        return api.ListPage[ConfigDoc](data=tuple(config_doc(r) for r in shown), next_cursor=nxt)

    async def config(self, user: str, provider: str, config_id: str) -> ConfigDoc:
        rows = await self.doors.call(user, "lab_judge_config_list", _uuid(provider, "provider"),
                                     _uuid(config_id, "configuration"), None, 1)
        if not rows:
            raise errors.NotFound("no such configuration")
        return config_doc(rows[0])

    async def budgets(self, user: str, provider: str) -> api.ListPage[BudgetDoc]:
        rows = await self.doors.call(user, "lab_judge_budget_list", _uuid(provider, "provider"))
        return api.ListPage[BudgetDoc](data=tuple(budget_doc(r) for r in rows))

    async def set_budget(self, user: str, provider: str, payer_ref: str, key: str,
                         body: BudgetBody) -> BudgetDoc:
        if body.limit.unit != "PROVIDER_USD":
            raise errors.InvalidRequest("a judge budget is PROVIDER_USD", param="limit.unit")
        digest = hashlib.sha256(str(scoped_id("judge.budget", provider, user, key,
                                              payer_ref)).encode()).hexdigest()
        row = await self.doors.call(user, "lab_judge_set_budget_keyed",
                                    _uuid(provider, "provider"), payer_ref,
                                    {"unit": "PROVIDER_USD", "value": body.limit.amount}, digest)
        return budget_doc(row)

    async def estimate(self, user: str, provider: str, body: EstimateBody) -> EstimateDoc:
        c = await self.config(user, provider, body.config_id)
        e = estimate_worst_case(self.rates, model=c.judge_model, samples=c.sample_size,
                                ceilings=DEFAULT_CEILINGS, at=self.clock())
        return EstimateDoc(config_id=c.config_id, judge_model=c.judge_model,
                           samples_max=c.sample_size, priced=e.priced,
                           price_version=e.price_version, per_sample=_usd(e.per_sample),
                           worst_case=_usd(e.worst_case_total), note=e.note)

    async def request_run(self, user: str, provider: str, key: str, body: RunBody) -> RunDoc:
        run = str(scoped_id("judge.run", provider, user, key))
        stored = await self.doors.call(user, "lab_judge_request_run", _uuid(provider, "provider"),
                                       run, body.config_id, body.payer_ref)
        if (uuid.UUID(str(stored["config_id"])), stored["payer_ref"]) != \
                (uuid.UUID(body.config_id), body.payer_ref):
            raise errors.IdempotencyConflict("this key already requested another run")
        return await self.run(user, provider, run)

    async def runs(self, user: str, provider: str, cursor: str | None,
                   limit: int) -> api.ListPage[RunDoc]:
        rows = await self.doors.call(user, "lab_judge_run_list", _uuid(provider, "provider"),
                                     None, decode_cursor(cursor), limit + 1)
        shown, nxt = page(rows, limit, lambda r: r["run_id"])
        return api.ListPage[RunDoc](data=tuple(run_doc(r) for r in shown), next_cursor=nxt)

    async def run(self, user: str, provider: str, run_id: str) -> RunDoc:
        rows = await self.doors.call(user, "lab_judge_run_list", _uuid(provider, "provider"),
                                     _uuid(run_id, "judge run"), None, 1)
        if not rows:
            raise errors.NotFound("no such judge run")
        return run_doc(rows[0])

    async def results(self, user: str, provider: str, run_id: str, cursor: str | None,
                      limit: int) -> api.ListPage[rubric.SampleResult]:
        rows = await self.doors.call(user, "lab_judge_run_results", _uuid(provider, "provider"),
                                     _uuid(run_id, "judge run"), decode_cursor(cursor),
                                     limit + 1)
        shown, nxt = page(rows, limit, lambda r: r["label_id"])
        known = dict(rubric.RUBRICS)
        if any(r["rubric_version"] not in known for r in shown):
            known.update({v: j.from_definition(row["definition"]) for v, row in
                          (await self.stored_rubrics(user, provider)).items()})
        return api.ListPage[rubric.SampleResult](
            data=tuple(rubric.project(r, known) for r in shown), next_cursor=nxt)

    async def cancel(self, user: str, provider: str, run_id: str, key: str) -> RunDoc:
        scoped_id("judge.cancel", provider, user, key)      # validated; a cancel is idempotent
        return run_doc(await self.doors.call(user, "lab_judge_cancel",
                                             _uuid(provider, "provider"),
                                             _uuid(run_id, "judge run")))

    async def calibration(self, user: str, provider: str, config_id: str) -> CalibrationView:
        c = await self.config(user, provider, config_id)
        return CalibrationView(config_id=c.config_id, **c.calibration.model_dump())

    async def feedback(self, user: str, provider: str, request_id: str) -> FeedbackDoc:
        scope = {"provider_org_id": _uuid(provider, "provider"),
                 "request_id": _uuid(request_id, "request")}
        signals = await self.doors.call(user, "lab_review_feedback", scope)
        reviews = await self.doors.call(user, "lab_trace_reviews", scope)
        return FeedbackDoc(
            signals=tuple(Signal(feedback_id=str(s["feedback_id"]), name=s["name"],
                                 value=s["value"], comment=s["comment"],
                                 author_role=s["author_role"], channel=s["channel"],
                                 created_at=str(s["created_at"])) for s in signals),
            reviews=tuple(review_doc(r) for r in reviews))

    async def review(self, user: str, provider: str, request_id: str, key: str,
                     body: ReviewBody) -> ReviewDoc:
        request = _uuid(request_id, "request")
        args: dict[str, Any] = {"provider_org_id": _uuid(provider, "provider"), "request_id": request,
                "review_id": str(scoped_id("trace.review", provider, user, key, request)),
                "input_hash": input_hash(body, request), "verdict": body.verdict}
        args.update({k: v for k, v in (("comment", body.comment), ("run_id", body.run_id),
                                       ("rubric_version", body.rubric_version)) if v is not None})
        return review_doc(await self.doors.call(user, "lab_trace_review", args))


def review_doc(row: dict[str, Any]) -> ReviewDoc:
    return ReviewDoc.model_validate({
        k: (str(v) if k in ("review_id", "request_id", "reviewer", "run_id", "created_at")
            and v is not None else v) for k, v in row.items() if k != "replayed"})
