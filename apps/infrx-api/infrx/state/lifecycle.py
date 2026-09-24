"""D10: the PostgreSQL adapter of F2C.a's three lifecycle ports (`contracts/v2/lifecycle.py`).

    store = PgLifecycle(connector(dsn), limits=settings, upload_window_s=..., grace_s=...,
                        claim_ttl_s=..., retention_s=...)
    # UploadRepository: create / acknowledge_put / complete / abort / resolve / expire
    # ReadinessStore:   admit_ready / readiness / claim_preparation
    # ContentLifecycle: register / references / candidates / claim / tombstone /
    #                   acknowledge_delete

Every operation is ONE call of one SECURITY DEFINER function (0019, 0020), so each is one
transaction on the database clock (R7); nothing is kept in this object between calls, so a
new instance over the same database IS another process (RV-02, RV-03). The windows are this
adapter's configuration, sent with the call that persists them (`expires_at`, `eligible_at`,
a claim's `expires_at`, a reference's retention) and never re-read afterwards.

Refusals: a lifecycle refusal arrives as P0001 `<code>: <detail>` with the hint
`refusal=<reason>` and becomes `lifecycle.refuse(reason, detail)` - the existing typed error,
carrying `.refusal`; a refusal after a write that must commit (an aborted ticket, R39) comes
back as data and is raised here. Everything else is `jobstore.domain_error`.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from ..contracts import errors, ids
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import IdempotencyRef, Lease, MediaRef, NormalizedRequest
from ..contracts.v2 import lifecycle as L
from ..contracts.v2.records import AccountingRegime
from .jobstore import Connect, PgJobStore, admission_of, admission_v2_of, domain_error

#: F2C.a's reference windows (`fakes/lifecycle.py`); a composition root passes its own.
#: P-25 is pending: `retention_s` is a fixture value until the approved serving TTL lands.
UPLOAD_WINDOW_S = DEFAULTS.processing_cache_ttl_s
GRACE_S = DEFAULTS.processing_cache_ttl_s
#: F2C.a D2 / M6: a claim must outlive the worst-case object-store call (M6: >= 300 s).
CLAIM_TTL_S = 300.0
RETENTION_S = DEFAULTS.processing_cache_ttl_s
#: A minted handle colliding with an existing one is ~2^-190; retrying is still cheaper
#: than reasoning about it.
_MINT_ATTEMPTS = 3
_TICKET = ("upload_handle", "org_id", "destination_ref", "constraints", "state", "created_at",
           "expires_at", "received", "finalized", "refusal")


def lifecycle_error(exc: Exception) -> Exception:
    """The typed refusal a database error stands for (see the module docstring)."""
    diag = getattr(exc, "diag", None)
    hint = (getattr(diag, "message_hint", None) or "") if diag is not None else ""
    if getattr(exc, "sqlstate", None) == "P0001":
        detail = (getattr(diag, "message_primary", None) or str(exc)).partition(": ")[2]
        if hint.startswith("refusal="):
            return L.refuse(L.LifecycleRefusal(hint.split("=", 1)[1]), detail)
        if hint.startswith("param="):
            code = (getattr(diag, "message_primary", "") or "").partition(": ")[0]
            kind = errors.UnsupportedParameter if code == "unsupported_parameter" \
                else errors.UnsupportedMedia
            return kind(detail, param=hint.split("=", 1)[1])
    return domain_error(exc)


def _raise_refusal(doc: Any) -> Any:
    """A refusal answered as data is `{"refusal": {reason|code, detail}, ...}`; a ticket's
    own `refusal` (why it was aborted) is a plain string and is data, not an error."""
    refusal = doc.get("refusal") if isinstance(doc, dict) else None
    if isinstance(refusal, dict):
        if "reason" in refusal:
            raise L.refuse(L.LifecycleRefusal(refusal["reason"]), refusal["detail"])
        from .jobstore import _BY_CODE
        raise _BY_CODE[refusal["code"]](refusal["detail"])
    return doc


def _ticket(doc: dict) -> L.UploadTicket:
    return L.UploadTicket.model_validate({k: doc[k] for k in _TICKET})


def _content(doc: dict) -> L.ContentObject:
    return L.ContentObject.model_validate({"claim": None, **doc})


def _readiness(doc: dict | None) -> L.ExecutionReadiness | None:
    if doc is None:
        return None
    return L.ExecutionReadiness.model_validate({
        "job_id": doc["job_id"], "org_id": doc["org_id"], "ready_at": doc["ready_at"],
        "sources": [{"content_id": s["content_id"], "generation": s["generation"],
                     "ref": s["ref"]} for s in doc["sources"]]})


class PgLifecycle:
    """`UploadRepository`, `ReadinessStore` and `ContentLifecycle` over PostgreSQL."""

    def __init__(self, connect: Connect, *, limits: PilotSettings = DEFAULTS,
                 upload_window_s: float = UPLOAD_WINDOW_S, grace_s: float = GRACE_S,
                 claim_ttl_s: float = CLAIM_TTL_S, retention_s: float = RETENTION_S) -> None:
        self._connect = connect
        self.limits = limits
        self.upload_window_s, self.grace_s = upload_window_s, grace_s
        self.claim_ttl_s, self.retention_s = claim_ttl_s, retention_s

    async def _call(self, function: str, args: dict[str, Any]) -> Any:
        from psycopg import Error, OperationalError
        from psycopg.types.json import Jsonb
        try:
            conn = await self._connect()
        except OperationalError:
            # RV-03: an unreachable authority is a typed, retryable answer - the collector
            # RETAINS and reports; it never reads silence as "unreferenced".
            raise errors.DependencyUnavailable("the lifecycle store is unreachable",
                                               retry_after_s=30) from None
        try:
            cursor = await conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),))
            (result,) = await cursor.fetchone()
        except OperationalError:
            raise errors.DependencyUnavailable("the lifecycle store is unreachable",
                                               retry_after_s=30) from None
        except Error as failed:
            raise lifecycle_error(failed) from None
        finally:
            await conn.close()
        return _raise_refusal(result)

    # --- UploadRepository ---------------------------------------------------------------
    async def create(self, org_id: str, constraints: L.UploadConstraints) -> L.UploadTicket:
        if not ids.is_request_id(org_id):
            raise errors.InvalidRequest("org_id must be a lowercase UUID")
        if not isinstance(constraints, L.UploadConstraints):
            raise L.refuse(L.LifecycleRefusal.invalid_constraints,
                           "constraints are an UploadConstraints record")
        from psycopg import errors as pg_errors
        for attempt in range(_MINT_ATTEMPTS):
            try:
                return _ticket(await self._call("upload_create", {
                    "org_id": org_id, "upload_handle": ids.new_upload_handle(),
                    "constraints": constraints.model_dump(mode="json",
                                                          exclude={"schema_version"}),
                    "window_s": self.upload_window_s}))
            except pg_errors.UniqueViolation:
                if attempt == _MINT_ATTEMPTS - 1:
                    raise errors.InternalError("could not mint an unused upload handle") \
                        from None
        raise AssertionError("unreachable")

    async def acknowledge_put(self, org_id: str, upload_handle: str, *, bytes: int,
                              digest: str) -> L.UploadTicket:
        if isinstance(bytes, bool) or not isinstance(bytes, int) or bytes < 0:
            raise errors.InvalidRequest("bytes is a nonnegative integer")
        return _ticket(await self._call("upload_acknowledge_put", {
            "org_id": org_id, "upload_handle": upload_handle, "bytes": bytes,
            "digest": digest}))

    async def complete(self, org_id: str, upload_handle: str, source: MediaRef
                       ) -> L.UploadTicket:
        return _ticket(await self._call("upload_complete", {
            "org_id": org_id, "upload_handle": upload_handle,
            "source": source.model_dump(mode="json"), "grace_s": self.grace_s}))

    async def abort(self, org_id: str, upload_handle: str,
                    refusal: L.LifecycleRefusal) -> L.UploadTicket:
        return _ticket(await self._call("upload_abort", {
            "org_id": org_id, "upload_handle": upload_handle,
            "refusal": L.LifecycleRefusal(refusal).value}))

    async def resolve(self, org_id: str, upload_handle: str) -> L.UploadTicket:
        return _ticket(await self._call("upload_resolve", {"org_id": org_id,
                                                           "upload_handle": upload_handle}))

    async def expire(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise errors.InvalidRequest("limit is a positive integer")
        return await self._call("upload_expire", {"limit": limit})

    # --- ReadinessStore -----------------------------------------------------------------
    async def admit_ready(self, request: NormalizedRequest, idem: IdempotencyRef,
                          expectation: L.AdmissionExpectation):
        if not isinstance(expectation, L.AdmissionExpectation):
            raise errors.InvalidRequest("an AdmissionExpectation from the runtime is required")
        regime = expectation.accounting_regime.value
        args = PgJobStore(None, limits=self.limits)._admit_args(regime, request, idem)
        doc = await self._call("admit_ready", {
            **args, "retention_s": self.retention_s,
            "expectation": expectation.model_dump(mode="json", exclude={"schema_version"})})
        admission = doc["admission"]
        record = admission_v2_of(admission) \
            if expectation.accounting_regime is AccountingRegime.credit \
            else admission_of(admission)
        return record, _readiness(doc["readiness"])

    async def readiness(self, job_id: str) -> L.ExecutionReadiness | None:
        if not ids.is_request_id(job_id):
            return None
        conn = await self._connect()
        try:
            cursor = await conn.execute("select infrx.readiness_doc(%s)", (job_id,))
            (doc,) = await cursor.fetchone()
        finally:
            await conn.close()
        return _readiness(doc)

    async def claim_preparation(self, job_id: str, worker_id: str) -> Lease:
        doc = await self._call("claim_preparation_ready", {
            "job_id": job_id, "worker_id": worker_id,
            "limits": {"preparation_lease_ttl_s": self.limits.preparation_lease_ttl_s,
                       "max_prepublication_retries": self.limits.max_prepublication_retries}})
        return Lease.model_validate(doc["lease"])

    async def cutover_check(self) -> dict:
        """The operator's cutover gate (0019 `readiness_cutover_check`): `ready` exactly
        when admission is paused and no preparing job lacks a marker."""
        conn = await self._connect()
        try:
            cursor = await conn.execute("select infrx.readiness_cutover_check()")
            (doc,) = await cursor.fetchone()
        finally:
            await conn.close()
        return doc

    # --- ContentLifecycle ---------------------------------------------------------------
    async def register(self, identity: L.ContentIdentity) -> L.ContentObject:
        return _content(await self._call("content_register", {
            "identity": identity.model_dump(mode="json", exclude={"schema_version"}),
            "grace_s": self.grace_s}))

    async def references(self, content_id: str) -> tuple[L.ContentReference, ...]:
        if not ids.is_request_id(content_id):
            raise L.refuse(L.LifecycleRefusal.not_found, "no such content")
        docs = await self._call("content_references", {"content_id": content_id})
        return tuple(L.ContentReference.model_validate(doc) for doc in docs)

    async def candidates(self, *, after: str | None, limit: int) -> L.ContentPage:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise errors.InvalidRequest("limit is a positive integer")
        start = _decode(after) if after is not None else (None, None)
        doc = await self._call("content_candidates", {
            "limit": min(limit, L.MAX_PAGE), "after_eligible_at": start[0],
            "after_content_id": start[1]})
        items = tuple(_content(item) for item in doc["items"])
        return L.ContentPage(items=items,
                             next_cursor=_encode(items[-1]) if doc["more"] else None)

    async def claim(self, content_id: str, generation: int, holder: str) -> L.DeletionClaim:
        if not ids.is_request_id(content_id):
            raise L.refuse(L.LifecycleRefusal.not_found, "no such content")
        return L.DeletionClaim.model_validate(await self._call("content_claim", {
            "content_id": content_id, "generation": generation, "holder": holder,
            "claim_ttl_s": self.claim_ttl_s}))

    async def tombstone(self, claim: L.DeletionClaim) -> L.Tombstone:
        return L.Tombstone.model_validate(await self._call("content_tombstone", {
            "claim": claim.model_dump(mode="json", exclude={"schema_version"})}))

    async def acknowledge_delete(self, tombstone: L.Tombstone) -> L.ContentObject:
        return _content(await self._call("content_acknowledge_delete", {
            "tombstone": tombstone.model_dump(mode="json", exclude={"schema_version"})}))


# The candidates cursor: opaque to the caller, `(eligible_at, content_id)` to the store - the
# fake's own encoding (`fakes/lifecycle.py`), so a page boundary means the same in both.
def _encode(row: L.ContentObject) -> str:
    raw = json.dumps([row.eligible_at.isoformat(), row.content_id]).encode()
    return "cc1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: object) -> tuple[str, str]:
    try:
        if not isinstance(cursor, str) or not cursor.startswith("cc1."):
            raise ValueError(cursor)
        body = cursor[4:] + "=" * (-len(cursor[4:]) % 4)
        at, content_id = json.loads(base64.urlsafe_b64decode(body))
        return datetime.fromisoformat(at).isoformat(), ids.require_request_id(content_id)
    except (ValueError, TypeError):
        raise errors.InvalidCursor("not a content cursor this store issued") from None


__all__ = ["CLAIM_TTL_S", "GRACE_S", "PgLifecycle", "RETENTION_S", "UPLOAD_WINDOW_S",
           "lifecycle_error"]
