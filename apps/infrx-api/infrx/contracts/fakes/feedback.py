"""In-memory FeedbackService.

Ownership comes from the durable job record, never from an eventually consistent
projection, so feedback submitted before the trace lands still resolves. The
client supplies a rating and a correction; channel, author role and calibration
membership are stamped by the server, and calibration needs an operator.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .. import errors
from ..records import (AuthorRole, Feedback, FeedbackChannel, FeedbackName, IdempotencyRef,
                       OutboxEvent, OutboxKind)
from ..wire import FeedbackSubmission
from .state import FakeJobStore
from .support import FailurePlan, failure_hooks

SERVER_SET = ("channel", "author_role", "author_principal", "org_id", "feedback_id",
              "created_at", "calibration_set")


class FakeFeedbackService:
    """`ports.FeedbackService`. One instance per channel, as G and C each have."""

    def __init__(self, jobs: FakeJobStore, *, channel: FeedbackChannel = FeedbackChannel.api,
                 failures: FailurePlan | None = None) -> None:
        self.jobs = jobs
        self.clock = jobs.clock
        self.ids = jobs.ids
        self.channel = channel
        self.failures = failure_hooks(failures)
        self.items: dict[str, Feedback] = {}
        self.idem: dict[tuple[str, str, str | None], tuple[str, str]] = {}
        self.outbox: list[OutboxEvent] = []
        self.audit: list[dict] = []          # append-only operator audit (r1 R19/R31)

    async def accept(self, auth, request_id: str, feedback: dict[str, Any],
                     idem: IdempotencyRef) -> Feedback:
        self.failures.before("accept")
        if not isinstance(feedback, dict):
            # A JSON array or scalar body is a 400, not a TypeError on the way in.
            raise errors.InvalidRequest("a feedback body is a JSON object")
        body = dict(feedback)
        for name in SERVER_SET:
            if name in body:
                raise errors.InvalidRequest(f"{name} is set by the server, not by the client")
        if not body:
            # r1 R3: an empty body is a 400, not a row that records nothing.
            raise errors.InvalidRequest("a feedback submission needs a name and a value")
        try:
            submission = FeedbackSubmission.model_validate({**body, "request_id": request_id})
        except ValidationError as exc:
            raise errors.InvalidRequest(f"invalid feedback submission: {exc.error_count()} errors")

        job = self.jobs.jobs.get(request_id)
        if job is None or job.request.org_id != auth.org_id:
            raise errors.NotFound(f"no request {request_id} owned by org {auth.org_id}")
        if idem.org_id != auth.org_id:
            raise errors.Forbidden("idempotency scope must be the caller's org")
        if idem.key is None:
            # r1 R3: `Idempotency-Key` is required on POST /v1/feedback and console
            # submit, so a retried submission can never become a second row.
            raise errors.InvalidRequest("an idempotency key is required for feedback")

        existing = self.idem.get(idem.scope)
        if existing is not None:
            payload_hash, feedback_id = existing
            if payload_hash != idem.payload_hash:
                raise errors.IdempotencyConflict("same feedback key, different payload")
            return self.items[feedback_id]

        now = self.clock.now()
        record = Feedback(
            feedback_id=self.ids.feedback_id(), request_id=request_id, org_id=auth.org_id,
            author_principal=auth.principal,
            # Console origin is not automatically an operator label.
            # r1 R31: `accept` is the customer path, on either channel. An operator
            # session using it is still a customer signal; operator provenance exists
            # only through `label_calibration`, and `judge` only through J's path.
            author_role=AuthorRole.customer,
            channel=self.channel, name=submission.name, value=submission.value,
            comment=submission.comment, calibration_set=None, created_at=now)
        self.items[record.feedback_id] = record
        self.idem[idem.scope] = (idem.payload_hash, record.feedback_id)
        # PostgreSQL commit plus outbox before the 201.
        self.outbox.append(OutboxEvent(event_id=self.ids.event_id(), aggregate_id=request_id,
                                       kind=OutboxKind.feedback_projection,
                                       payload={"feedback_id": record.feedback_id},
                                       available_at=now))
        self.failures.after_commit("accept")
        return record

    async def label_calibration(self, auth, request_id: str, label: str,
                                idem: IdempotencyRef) -> Feedback:
        """r1 R31/R19/R26: the only path to `author_role=operator` with calibration
        membership. Operator only, platform-wide (the tenant comes from the row, not
        from the operator's session), idempotent and audited."""
        self.failures.before("label_calibration")
        if not auth.is_operator:
            raise errors.Forbidden("labelling a calibration set requires a platform operator")
        if not isinstance(label, str) or not label.strip():
            raise errors.InvalidRequest("a calibration label is required")
        if idem.key is None:
            raise errors.InvalidRequest("an idempotency key is required for a calibration label")
        job = self.jobs.jobs.get(request_id)
        if job is None:
            raise errors.NotFound(f"no request {request_id}")
        # R26: the label's organization is the *row's*, never the operator's session.
        org_id = job.request.org_id
        if idem.org_id != org_id:
            raise errors.Forbidden("the idempotency scope must name the labelled row's org")
        existing = self.idem.get(idem.scope)
        if existing is not None:
            payload_hash, feedback_id = existing
            if payload_hash != idem.payload_hash:
                raise errors.IdempotencyConflict("same label key, different payload")
            return self.items[feedback_id]
        now = self.clock.now()
        record = Feedback(
            feedback_id=self.ids.feedback_id(), request_id=request_id, org_id=org_id,
            author_principal=auth.principal, author_role=AuthorRole.operator,
            channel=FeedbackChannel.console, name=FeedbackName.comment,
            value=f"calibration:{label}", calibration_set=label, created_at=now)
        self.items[record.feedback_id] = record
        self.idem[idem.scope] = (idem.payload_hash, record.feedback_id)
        self.audit.append({"at": now, "event": "label_calibration", "label": label,
                           "operator": auth.principal, "request_id": request_id,
                           "org_id": org_id, "feedback_id": record.feedback_id})
        self.outbox.append(OutboxEvent(event_id=self.ids.event_id(), aggregate_id=request_id,
                                       kind=OutboxKind.feedback_projection,
                                       payload={"feedback_id": record.feedback_id,
                                                "calibration_set": label},
                                       available_at=now))
        self.failures.after_commit("label_calibration")
        return record

    async def list_owned(self, auth, request_id: str) -> tuple[Feedback, ...]:
        job = self.jobs.jobs.get(request_id)
        if job is None or job.request.org_id != auth.org_id:
            raise errors.NotFound(f"no request {request_id} owned by org {auth.org_id}")
        return tuple(item for item in self.items.values()
                     if item.request_id == request_id and item.org_id == auth.org_id)
