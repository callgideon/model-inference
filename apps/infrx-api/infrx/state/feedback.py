"""D6F: the PostgreSQL `FeedbackService` (ports.FeedbackService).

Every write is ONE call of one SECURITY DEFINER function (`accept_feedback`,
`label_calibration`; the D6F migration), which commits the row, its idempotency record and a
`feedback_projection` outbox event together - so the acknowledgment is never ahead of
durability. Ownership is the durable job row, never a trace projection. This module validates
what the port promises as typed 400/403s before the call, shapes the record and applies the
one visibility rule (`records.visible_feedback`) to every answer a viewer gets, replays
included (R54). The SQL refuses the same things again, so a caller that skips this module
still cannot store a spoofed author.

Disabled unless the `feedback` flag row is enabled (a missing row is off): the writes then
raise `DependencyUnavailable`. Nothing composes this service yet (G4F/C3F wire it).
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..contracts import errors, ids
from ..contracts.limits import MAX_FEEDBACK_TEXT_CHARS, MAX_RUBRIC_VERSION, MIN_RUBRIC_VERSION
from ..contracts.records import (CalibrationLabel, Feedback, FeedbackChannel, IdempotencyRef,
                                 visible_feedback)
from ..contracts.wire import FeedbackSubmission
from .jobstore import Connect, domain_error


class PgFeedbackService:
    """One instance per channel, as G (api) and C (console) each have."""

    def __init__(self, connect: Connect, *,
                 channel: FeedbackChannel = FeedbackChannel.api) -> None:
        self._connect = connect
        self.channel = channel

    async def _call(self, function: str, args: dict[str, Any]) -> Any:
        from psycopg import Error
        from psycopg.types.json import Jsonb
        conn = await self._connect()
        try:
            cursor = await conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),))
            (result,) = await cursor.fetchone()
        except Error as failed:
            if failed.sqlstate == "0A000":
                raise errors.DependencyUnavailable("feedback is not enabled") from None
            raise domain_error(failed) from None
        finally:
            await conn.close()
        return result

    async def accept(self, auth, request_id: str, feedback: dict[str, Any],
                     idem: IdempotencyRef) -> Feedback:
        return (await self.accept_with_replay(auth, request_id, feedback, idem))[0]

    async def accept_with_replay(self, auth, request_id: str, feedback: dict[str, Any],
                                 idem: IdempotencyRef) -> tuple[Feedback, bool]:
        """`accept`, and whether it was a replay (WR-G4F-2: the route's `replayed`): a replay
        answers the stored row, whose id is not the one this call generated."""
        if not isinstance(feedback, dict):
            raise errors.InvalidRequest("a feedback submission is one {name, value} object")
        try:     # extra="forbid": a provenance field (author, channel, marker) is refused
            body = FeedbackSubmission.model_validate({**feedback, "request_id": request_id})
        except ValidationError as exc:
            raise errors.InvalidRequest(f"invalid feedback submission: {exc.error_count()} errors")
        if idem.org_id != auth.org_id:
            raise errors.Forbidden("idempotency scope must be the caller's org")
        if idem.key is None:
            raise errors.InvalidRequest("an idempotency key is required for feedback")
        feedback_id = ids.new_feedback_id()
        row = await self._call("accept_feedback", {
            "org_id": auth.org_id, "principal": auth.principal,
            "by_operator": bool(auth.is_operator), "channel": self.channel.value,
            "request_id": request_id, "feedback_id": feedback_id,
            "body": body.model_dump(mode="json", include={"name", "value", "comment"},
                                    exclude_none=True),
            "idem": idem.model_dump(mode="json")})
        return visible_feedback((Feedback.model_validate(row),),
                                operator=bool(auth.is_operator))[0], \
            row["feedback_id"] != feedback_id

    async def scrub(self, org_id: str, request_id: str, *, actor: str, reason: str) -> int:
        """T3's deletion reaching durable feedback (0035): the request's comments and free
        text are removed under a receipt; the number of rows changed (0 on a repeat)."""
        return (await self._call("scrub_feedback", {
            "org_id": org_id, "request_id": request_id, "actor": actor,
            "reason": reason}))["scrubbed"]

    async def label_calibration(self, auth, request_id: str, label: str, rubric_version: int,
                                idem: IdempotencyRef, *, comment: str | None = None) -> Feedback:
        if not auth.is_operator:
            raise errors.Forbidden("labelling a calibration set requires a platform operator")
        if label not in tuple(CalibrationLabel):
            raise errors.InvalidRequest("a calibration label is one of "
                                        f"{', '.join(v.value for v in CalibrationLabel)}")
        if isinstance(rubric_version, bool) or not isinstance(rubric_version, int) \
                or not MIN_RUBRIC_VERSION <= rubric_version <= MAX_RUBRIC_VERSION:
            raise errors.InvalidRequest(f"rubric_version must be an integer in "
                                        f"{MIN_RUBRIC_VERSION}..{MAX_RUBRIC_VERSION}")
        if comment is not None and (not isinstance(comment, str)
                                    or len(comment) > MAX_FEEDBACK_TEXT_CHARS):
            raise errors.InvalidRequest(
                f"a comment is text of at most {MAX_FEEDBACK_TEXT_CHARS} characters")
        if idem.key is None:
            raise errors.InvalidRequest("an idempotency key is required for a calibration label")
        row = await self._call("label_calibration", {
            "principal": auth.principal, "is_operator": True, "request_id": request_id,
            "feedback_id": ids.new_feedback_id(), "label": label,
            "rubric_version": rubric_version, "comment": comment,
            "idem": idem.model_dump(mode="json")})
        return Feedback.model_validate(row)

    async def list_owned(self, auth, request_id: str) -> tuple[Feedback, ...]:
        rows = await self._call("request_feedback", {"request_id": request_id,
                                                     "org_id": auth.org_id})
        return visible_feedback(tuple(Feedback.model_validate(r) for r in rows),
                                operator=bool(auth.is_operator))

    async def list_calibration(self, auth, request_id: str) -> tuple[Feedback, ...]:
        if not auth.is_operator:
            raise errors.Forbidden("calibration labels are operator data")
        rows = await self._call("request_feedback", {"request_id": request_id,
                                                     "calibration": True})
        return tuple(Feedback.model_validate(r) for r in rows)
