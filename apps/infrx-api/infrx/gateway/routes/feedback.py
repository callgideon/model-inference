"""G4F: `POST /v1/feedback`, the owned feedback adapter over D6F's `FeedbackService`.

    POST /v1/feedback   {request_id, name, value, comment?} + Idempotency-Key
                        -> 201 `FeedbackAccepted` once PostgreSQL committed it

The service owns every rule that needs the database: ownership by the durable job (another
org's request is `not_found`), suspension, replay versus `idempotency_conflict`, the
`feedback` flag (off: `dependency_unavailable`, a 503). What only the route can do is:

* **identity before any body byte**, and the author is that identity: the key's org and
  principal go to the service, and a body naming any provenance field (author, role,
  channel, org, calibration) is a 400 before the service is called (R3/R31);
* **validate the signal and the key**: the frozen `FeedbackSubmission`, a required,
  bounded `Idempotency-Key`, and a payload digest over the canonical signal so a retry in
  another spelling replays and a changed signal conflicts;
* **acknowledge only durable acceptance**: the 201 is rendered from the row `accept`
  returned, after it returned; any refusal or failure is the typed error, never an ack.

No financial logic: feedback moves no money. Mounted by nobody until the coordinator adds
it to `app.ROUTERS`, and even then only when the composition put a service on
`rt.feedback`, which it does only when the deployment enables it (off by default).
"""
from __future__ import annotations

import hashlib

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...auth.context import AuthResolver
from ...contracts import codec, errors, ids, wire
from . import intake
from .ingress import install_error_handlers
from .validate import idempotency

FEEDBACK_PATH = "/v1/feedback"
OPERATION = "feedback"          # D6F's idempotency operation for `accept`
# The largest legal signal is a 4000-character value and comment; an astral character
# JSON-escaped is 12 bytes (a surrogate pair of \uXXXX), so 96 KB, under this cap.
MAX_FEEDBACK_BYTES = 131_072


def register(app, rt, service=None, new_request_id=ids.new_request_id):
    """Mount `POST /v1/feedback` over `service` (default `rt.feedback`). Without one
    nothing is mounted and `None` is returned."""
    install_error_handlers(app, new_request_id)
    service = service if service is not None else getattr(rt, "feedback", None)
    if service is None:
        return None
    auth = AuthResolver(rt)
    limits = rt.settings.pilot

    @app.post(FEEDBACK_PATH)
    @intake.guard(new_request_id, limits)
    async def submit_feedback(request: Request, request_id: str):
        context = await auth.context(request)
        intake.check_content_type(request)
        raw = await intake.read_body(request, max_bytes=MAX_FEEDBACK_BYTES,
                                     timeout_s=limits.intake_timeout_s, clock=rt.clock)
        body = intake.parse_object(intake.decode_utf8(raw)) if raw else {}
        try:     # extra="forbid": a provenance field is refused, never passed on
            submission = wire.FeedbackSubmission.model_validate(body)
        except ValidationError as exc:
            raise errors.InvalidRequest(
                f"invalid feedback submission: {exc.error_count()} errors") from None
        if request.headers.get(wire.HEADER_IDEMPOTENCY_KEY) is None:
            raise errors.InvalidRequest("an Idempotency-Key is required for feedback",
                                        param=wire.HEADER_IDEMPOTENCY_KEY)
        digest = "sha256:" + hashlib.sha256(codec.compact_bytes(submission)).hexdigest()
        idem = idempotency(context, request.headers, digest, OPERATION)
        signal = submission.model_dump(mode="json", exclude={"request_id"}, exclude_none=True)
        stored = await service.accept(context, submission.request_id, signal, idem)
        # ponytail: `replayed` stays false - `accept` does not say whether it replayed;
        # D6F reporting it (request WR-G4F-2) is the upgrade.
        accepted = wire.FeedbackAccepted(
            feedback_id=stored.feedback_id, request_id=stored.request_id,
            channel=stored.channel, author_role=stored.author_role, created_at=stored.created_at)
        return JSONResponse(accepted.model_dump(mode="json"), status_code=201,
                            headers={wire.HEADER_INFERENCE_ID: request_id})

    return service
