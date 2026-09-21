"""Record builders shared by every conformance case.

Records only: no fake is imported here, so a real adapter's factory can use the
same builders. Two organizations exist on purpose, so every ownership case can
prove a cross-tenant call is a 404.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..limits import DEFAULTS
from ..records import (AuthContext, ChunkEventType, ConsentSnapshot, EngineEvent, ExecutionMode,
                       FeedbackName, IdempotencyRef, JobState, MediaKind, MediaRef,
                       NormalizedRequest, PriceSnapshot, Role, TerminalCause, TerminalOutcome,
                       TraceEnvelope, TraceMode, Usage)


def default_deadline_s(mode: ExecutionMode = ExecutionMode.stream,
                       limits=DEFAULTS) -> float:
    """The longest deadline r1 R29 lets a store accept: preparation + queue +
    generation on that mode's budgets. A case wanting a longer horizon widens a
    budget through the factory's `limits`, because the store checks against its own."""
    queue = (limits.queue_wait_async_s if mode is ExecutionMode.async_
             else limits.queue_wait_interactive_s)
    return limits.preparation_timeout_s + queue + limits.generation_timeout_s

ORG_A = "1a1a1a1a-0000-4000-8000-000000000001"
ORG_B = "2b2b2b2b-0000-4000-8000-000000000002"
KEY_A = "3c3c3c3c-0000-4000-8000-000000000003"
KEY_B = "4d4d4d4d-0000-4000-8000-000000000004"
MODEL = "nemostation/marlin-2b@2026-09-01"


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def price(input_rate: str = "0.20", output_rate: str = "0.60", *, captured_at=None) -> PriceSnapshot:
    return PriceSnapshot(price_version="pv_test", model_revision=MODEL,
                         input_rate_per_million=Decimal(input_rate),
                         output_rate_per_million=Decimal(output_rate),
                         token_rules_version="tr_v1",
                         captured_at=captured_at or "2026-09-01T00:00:00Z")


def consent(org_id: str = ORG_A, *, mode: TraceMode = TraceMode.full, evaluation: bool = True,
            effective_at=None, revoked_at=None) -> ConsentSnapshot:
    return ConsentSnapshot(org_id=org_id, consent_version=1, trace_mode=mode,
                           content_retention_days=30, evaluation_consent=evaluation,
                           effective_at=effective_at or "2026-09-01T00:00:00Z",
                           revoked_at=revoked_at)


def auth(org_id: str = ORG_A, key_id: str = KEY_A, role: Role = Role.owner) -> AuthContext:
    return AuthContext(org_id=org_id, key_id=key_id, principal=key_id, role=role,
                       entitlement_version=1)


def media(org_id: str = ORG_A, handle: str = "upl_conformancefixture0000000000000000001",
          *, kind: MediaKind = MediaKind.url, nbytes: int = 4_194_304) -> MediaRef:
    return MediaRef(org_id=org_id, handle=handle, kind=kind, digest=digest(handle), bytes=nbytes,
                    mime="video/mp4", storage_ref=f"media/{org_id}/v1/source", duration_s=12.5)


def request(harness, *, org_id: str = ORG_A, key_id: str = KEY_A,
            mode: ExecutionMode = ExecutionMode.stream, max_input_tokens: int = 30_720,
            max_output_tokens: int = 2_048, snapshot: PriceSnapshot | None = None,
            refs: tuple[MediaRef, ...] = (), deadline_s: float | None = None,
            parameters: dict[str, Any] | None = None) -> NormalizedRequest:
    """A normalized request on the harness's clock and id sequence.

    The price snapshot travels in `parameters["price_snapshot"]` so a fake can
    snapshot it without a price table; an adapter with a real `price_versions`
    relation may ignore it and seed its own, because the cases assert against
    `admission.price_snapshot`, never against this builder's copy.
    """
    snapshot = snapshot or price()
    deadline_s = default_deadline_s(mode) if deadline_s is None else deadline_s
    now = harness.clock.now()
    request_id = harness.ids.uuid()
    return NormalizedRequest(
        request_id=request_id, org_id=org_id, key_id=key_id, model_revision=MODEL,
        messages=({"role": "user", "content": "Describe this clip."},),
        parameters={"price_snapshot": snapshot.model_dump(mode="json"), **(parameters or {})},
        payload_ref=f"payloads/{org_id}/{request_id}.json", payload_digest=digest(request_id),
        media=refs, execution_mode=mode, max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens, created_at=now,
        deadline_at=now + timedelta(seconds=deadline_s), trace_policy=consent(org_id))


def idem(req: NormalizedRequest, key: str | None = "idem-1", *,
         operation: str = "chat.completions", payload: str | None = None) -> IdempotencyRef:
    return IdempotencyRef(org_id=req.org_id, operation=operation, key=key,
                          payload_hash=digest(payload if payload is not None else req.request_id))


def hold_for(req: NormalizedRequest, snapshot: PriceSnapshot | None = None) -> Decimal:
    snapshot = snapshot or price()
    return snapshot.maximum_hold(req.max_input_tokens, req.max_output_tokens)


def usage(prompt_tokens: int = 1200, completion_tokens: int = 340) -> Usage:
    return Usage.of(prompt_tokens, completion_tokens)


UNSET = object()          # so `tokens=None` can mean "no authoritative usage"


def outcome(job_id: str, harness, *, cause: TerminalCause = TerminalCause.completed,
            state: JobState = JobState.succeeded, tokens: Usage | None = UNSET,
            result_ref: str | None = "results/test/result.json") -> TerminalOutcome:
    """The worker's proposal. The store recomputes settlement and debit."""
    from ..records import SettlementState
    return TerminalOutcome(job_id=job_id, state=state, cause=cause,
                           usage=usage() if tokens is UNSET else tokens,
                           result_ref=result_ref,
                           settlement_state=SettlementState.released_free,
                           settled_at=harness.clock.now())


def feedback(name: FeedbackName = FeedbackName.rating, value: object = 4,
             comment: str | None = None) -> dict[str, Any]:
    """A client feedback body (r1 R3): one signal, `name` fixing the value's type."""
    body: dict[str, Any] = {"name": name.value, "value": value}
    if comment is not None:
        body["comment"] = comment
    return body


def events(*contents: str) -> tuple[EngineEvent, ...]:
    return tuple(EngineEvent(type=ChunkEventType.delta, payload={"content": content})
                 for content in contents)


def trace(request_id: str, *, org_id: str = ORG_A, mode: TraceMode = TraceMode.full,
          content_bytes: int = 8_192, metadata_bytes: int = 512, harness=None) -> TraceEnvelope:
    started = harness.clock.now() if harness is not None else "2026-09-20T12:00:00Z"
    return TraceEnvelope(request_id=request_id, org_id=org_id, key_id=KEY_A, mode=mode,
                         started_at=started, content_complete=content_bytes > 0,
                         content_ref=(f"traces/{org_id}/{request_id}.json.zst"
                                      if content_bytes else None),
                         content_bytes=content_bytes, metadata_bytes=metadata_bytes,
                         model_revision=MODEL, price_version="pv_test")
