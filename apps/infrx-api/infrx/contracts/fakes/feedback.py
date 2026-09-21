"""In-memory FeedbackService.

Ownership comes from the durable job record, never from an eventually consistent
projection, so feedback submitted before the trace lands still resolves. The
client supplies one named signal; channel, author role, calibration membership and
the rubric version are stamped by the server, and calibration needs an operator.

r1 R43 fixes one persisted shape for all of it: a calibration label is a
`Feedback` row with `name=calibration_label`, a closed-vocabulary `value`,
`calibration_set=True` and an integer `rubric_version`. R35/R41 then decide who
may read one: `list_owned` hides labels from a customer and reports an
operator-authored principal as `platform`; `list_calibration` is the operator view.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .. import errors
from ..limits import MAX_FEEDBACK_TEXT_CHARS, MAX_RUBRIC_VERSION, MIN_RUBRIC_VERSION
from ..records import (AuthorRole, CalibrationLabel, Feedback, FeedbackChannel, FeedbackName,
                       IdempotencyRef, OutboxEvent, OutboxKind)
from ..wire import FeedbackList, FeedbackSubmission
from .state import FakeJobStore
from .support import FailurePlan, failure_hooks

SERVER_SET = ("channel", "author_role", "author_principal", "org_id", "feedback_id",
              "created_at", "calibration_set", "rubric_version")


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
        # r1 R33: the injectable suspension source, which is the JobStore's - one
        # organization cannot be suspended for admission and live for feedback.
        self.is_suspended = lambda org_id: org_id in jobs.suspended_orgs

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
        if self.is_suspended(auth.org_id):
            # r1 R33: suspension gates new work and configuration changes. Every read
            # (`list_owned` below) keeps working, so a suspended tenant can still see
            # what it submitted before.
            raise errors.OrgSuspended(f"org {auth.org_id} is suspended")

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
            comment=submission.comment, calibration_set=False, rubric_version=None,
            created_at=now)
        self.items[record.feedback_id] = record
        self.idem[idem.scope] = (idem.payload_hash, record.feedback_id)
        # PostgreSQL commit plus outbox before the 201.
        self.outbox.append(OutboxEvent(event_id=self.ids.event_id(), aggregate_id=request_id,
                                       kind=OutboxKind.feedback_projection,
                                       payload={"feedback_id": record.feedback_id},
                                       available_at=now))
        self.failures.after_commit("accept")
        return record

    async def label_calibration(self, auth, request_id: str, label: str, rubric_version: int,
                                idem: IdempotencyRef, *, comment: str | None = None) -> Feedback:
        """r1 R31/R19/R26/R43: the only path to `author_role=operator` with calibration
        membership. Operator only, platform-wide (the tenant comes from the row, not
        from the operator's session), idempotent and audited."""
        self.failures.before("label_calibration")
        if not auth.is_operator:
            raise errors.Forbidden("labelling a calibration set requires a platform operator")
        if label not in tuple(CalibrationLabel):
            # r1 R43: a closed vocabulary, so "partially-correct" or a free-text note
            # cannot quietly become a calibration verdict nothing can aggregate.
            raise errors.InvalidRequest(
                "a calibration label is one of "
                f"{', '.join(value.value for value in CalibrationLabel)}")
        if isinstance(rubric_version, bool) or not isinstance(rubric_version, int) \
                or not MIN_RUBRIC_VERSION <= rubric_version <= MAX_RUBRIC_VERSION:
            raise errors.InvalidRequest(
                f"rubric_version must be an integer in {MIN_RUBRIC_VERSION}"
                f"..{MAX_RUBRIC_VERSION}")
        if comment is not None and (not isinstance(comment, str)
                                   or len(comment) > MAX_FEEDBACK_TEXT_CHARS):
            raise errors.InvalidRequest(
                f"a comment is text of at most {MAX_FEEDBACK_TEXT_CHARS} characters")
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
            channel=FeedbackChannel.console, name=FeedbackName.calibration_label,
            value=label, comment=comment, calibration_set=True,
            rubric_version=rubric_version, created_at=now)
        self.items[record.feedback_id] = record
        self.idem[idem.scope] = (idem.payload_hash, record.feedback_id)
        self.audit.append({"at": now, "event": "label_calibration", "label": label,
                           "rubric_version": rubric_version, "operator": auth.principal,
                           "request_id": request_id, "org_id": org_id,
                           "feedback_id": record.feedback_id})
        self.outbox.append(OutboxEvent(event_id=self.ids.event_id(), aggregate_id=request_id,
                                       kind=OutboxKind.feedback_projection,
                                       payload={"feedback_id": record.feedback_id,
                                                "calibration_set": True,
                                                "rubric_version": rubric_version},
                                       available_at=now))
        self.failures.after_commit("label_calibration")
        return record

    async def list_owned(self, auth, request_id: str) -> tuple[Feedback, ...]:
        """r1 R35/R41: a non-operator caller sees no calibration labels and no operator
        principal. The filter is in the port rather than in its callers, because every
        route that forgets it publishes operator data to a tenant.

        It is the *same* projection `wire.FeedbackList.for_viewer` applies, called rather
        than repeated: two copies of a visibility rule is one copy that gets fixed.
        """
        job = self.jobs.jobs.get(request_id)
        if job is None or job.request.org_id != auth.org_id:
            raise errors.NotFound(f"no request {request_id} owned by org {auth.org_id}")
        rows = tuple(item for item in self.items.values()
                     if item.request_id == request_id and item.org_id == auth.org_id)
        return FeedbackList.for_viewer(rows, operator=auth.is_operator).items

    async def list_calibration(self, auth, request_id: str) -> tuple[Feedback, ...]:
        """r1 R35, mirroring the console's `calibration.list`: the operator-only view.

        Platform-wide (R26): a calibration set spans tenants, so the row's own org is
        the tenant and the operator's session org is not a filter.
        """
        if not auth.is_operator:
            raise errors.Forbidden("calibration labels are operator data")
        if self.jobs.jobs.get(request_id) is None:
            raise errors.NotFound(f"no request {request_id}")
        return tuple(item for item in self.items.values()
                     if item.request_id == request_id and item.calibration_set)
