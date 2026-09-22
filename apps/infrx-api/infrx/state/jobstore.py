"""D2: the PostgreSQL `JobStore` - admission, preparation and the dispatch outbox.

Every operation is ONE call of one SECURITY DEFINER function (the 06 mutation boundary),
so each is one transaction on the database clock (R7); this module only shapes records
and maps refusals. The function bodies live in `apps/app/supabase/migrations/0011_*`
onward; `credit_schema.py` records their names and the lock order.

Refusals raised in SQL arrive as SQLSTATE P0001 with the message `<error_code>: detail`
and become the `DomainError` of that code. D1R's seams keep their own SQLSTATEs.

Operations D3-D5 own (`claim`, `heartbeat`, `cancel`, `complete`, `recover`, inference
`load_work`) raise `NotImplementedError` naming the task: a fail-closed boundary, never
a silent success.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import (Admission, Budgets, IdempotencyRef, IndexEvent, Lease,
                                 MediaRef, NormalizedRequest, ReservationKind,
                                 TerminalOutcome)
from ..contracts.v2.records import AdmissionV2

#: `async () -> psycopg.AsyncConnection` in autocommit, acting as `service_role`.
Connect = Callable[[], Awaitable[Any]]

#: The admission fields the port's `Admission` record carries (the SQL document has more).
_ADMISSION_FIELDS = tuple(name for name in Admission.model_fields if name != "schema_version")
_OUTCOME_FIELDS = ("job_id", "state", "cause", "result_ref", "settlement_state", "debit",
                   "settled_at", "reconcile_after")


def connector(dsn: str) -> Connect:
    """The simplest `Connect`: a fresh connection per operation, `set role service_role`
    (0004: BYPASSRLS is not inherited, so the role must be SET, as PostgREST does).
    ponytail: one connection per call; a psycopg pool with the same `configure` hook
    when the gateway wires `DATABASE_POOL_*`."""
    async def connect():
        import psycopg
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        await conn.execute("set role service_role")
        return conn
    return connect


def _codes() -> dict[str, type[errors.DomainError]]:
    found: dict[str, type[errors.DomainError]] = {}
    stack = [errors.DomainError]
    while stack:
        cls = stack.pop()
        found.setdefault(cls.code, cls)
        stack.extend(cls.__subclasses__())
    return found


_BY_CODE = _codes()


def domain_error(exc: Exception) -> Exception:
    """The typed refusal a database error stands for, or the error itself (a bug)."""
    diag = getattr(exc, "diag", None)
    state = getattr(exc, "sqlstate", None)
    message = (getattr(diag, "message_primary", None) or str(exc)).strip()
    if state == "P0001":
        code, _, detail = message.partition(": ")
        cls = _BY_CODE.get(code)
        if cls is not None:
            hint = getattr(diag, "message_hint", None) or ""
            if hint.startswith("retry_after="):
                return cls(detail, retry_after_s=int(hint.split("=", 1)[1]))
            return cls(detail)
    if state == "23514" and getattr(diag, "constraint_name", None) == \
            "credit_wallets_reserved_within_total":
        return errors.InsufficientCredit("the hold exceeds the wallet's available CREDIT")
    if state == "55000":
        return errors.DependencyUnavailable(f"maintenance: {message}", retry_after_s=30)
    if state == "P0002":
        return errors.NotFound(message)
    if state == "22023":
        return errors.InvalidRequest(message)
    return exc


def _limits(limits: PilotSettings) -> dict[str, Any]:
    return {name: getattr(limits, name) for name in (
        "max_active_jobs", "max_active_jobs_per_org", "max_active_jobs_per_key",
        "max_preparing_jobs", "journal_job_reserve_bytes", "journal_total_bytes",
        "max_output_tokens", "max_context_tokens", "idempotency_ttl_s")}


def _outcome(doc: dict | None) -> TerminalOutcome | None:
    if not doc:
        return None
    return TerminalOutcome.model_validate({k: doc[k] for k in _OUTCOME_FIELDS})


def admission_of(doc: dict) -> Admission:
    return Admission.model_validate({k: doc[k] for k in _ADMISSION_FIELDS if k in doc})


def admission_v2_of(doc: dict) -> AdmissionV2:
    return AdmissionV2.model_validate({
        "request_id": doc["request_id"], "job_handle": doc["job_handle"],
        "org_id": doc["org_id"], "wallet_id": doc["wallet_id"], "pins": doc["pins"],
        "rate_card": doc["rate_card"],
        "maximum_hold": doc["maximum_hold"],
        "admitted_at": doc["admitted_at"], "replayed": doc["replayed"]})


class PgJobStore:
    """`ports.JobStore` over PostgreSQL. `limits` is the store's own configuration: caps,
    budgets and ceilings come from here, never from a caller (R4, R53)."""

    def __init__(self, connect: Connect, *, limits: PilotSettings = DEFAULTS) -> None:
        self._connect = connect
        self.limits = limits

    async def _call(self, function: str, args: dict[str, Any]) -> Any:
        from psycopg import Error
        from psycopg.types.json import Jsonb
        conn = await self._connect()
        try:
            cursor = await conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),))
            (result,) = await cursor.fetchone()
        except Error as failed:
            raise domain_error(failed) from None
        finally:
            await conn.close()
        return result

    async def _query(self, sql: str, params: tuple) -> list[tuple]:
        conn = await self._connect()
        try:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()
        finally:
            await conn.close()

    def _admit_args(self, regime: str, request: NormalizedRequest,
                    idem: IdempotencyRef) -> dict[str, Any]:
        return {"regime": regime, "request": request.model_dump(mode="json"),
                "idem": idem.model_dump(mode="json"), "limits": _limits(self.limits),
                "budgets": Budgets.of(self.limits, request.execution_mode).model_dump(
                    mode="json")}

    # --- admission (D2 item 1) ---------------------------------------------------
    async def admit(self, request: NormalizedRequest, idem: IdempotencyRef,
                    caps: tuple[ReservationKind, ...] = ()) -> Admission:
        """The v1 port, legacy USD regime: one `infrx.admit` transaction."""
        for kind in caps:
            if kind not in tuple(ReservationKind):
                raise errors.InvalidRequest(f"{kind!r} is not a reservation kind")
        return admission_of(await self._call("admit",
                                             self._admit_args("legacy_usd", request, idem)))

    async def admit_credit(self, request: NormalizedRequest,
                           idem: IdempotencyRef) -> AdmissionV2:
        """The CREDIT regime (06a): `request.model_revision` is the alias or R62 pin the
        caller asked for; the store resolves the pins and the wallet (R66, from the key's
        identity) in the same transaction as the hold."""
        return admission_v2_of(await self._call("admit",
                                                self._admit_args("credit", request, idem)))

    async def _owned_doc(self, org_id: str, job_handle: str) -> dict:
        rows = await self._query(
            "select infrx.job_admission(j.request_id) from infrx.jobs j "
            "where j.org_id = %s and j.job_handle = %s", (org_id, job_handle))
        if not rows:
            # Cross-tenant and unknown are indistinguishable on purpose.
            raise errors.NotFound(f"no job {job_handle} owned by org {org_id}")
        return rows[0][0]

    async def get_owned(self, org_id: str,
                        job_handle: str) -> tuple[Admission, TerminalOutcome | None]:
        doc = await self._owned_doc(org_id, job_handle)
        if doc["accounting_regime"] != "legacy_usd":
            raise errors.NotFound(f"job {job_handle} is a CREDIT job: use get_owned_credit")
        return admission_of(doc), _outcome(doc["outcome"])

    async def get_owned_credit(self, org_id: str,
                               job_handle: str) -> tuple[AdmissionV2, TerminalOutcome | None]:
        doc = await self._owned_doc(org_id, job_handle)
        if doc["accounting_regime"] != "credit":
            raise errors.NotFound(f"job {job_handle} is not a CREDIT job")
        return admission_v2_of(doc), _outcome(doc["outcome"])

    # --- preparation (D2 item 2) ----------------------------------------------------
    @staticmethod
    def _answer(doc: dict) -> dict:
        """R39: a refusal that followed a committed terminalization comes back as data,
        because raising inside the function would have rolled the terminalization back."""
        refusal = doc.get("refusal") if isinstance(doc, dict) else None
        if refusal:
            raise _BY_CODE[refusal["code"]](refusal["detail"])
        return doc

    async def claim_preparation(self, job_id: str, worker_id: str) -> Lease:
        """r1 R46/R52: a fenced preparation lease; one live lease per job, the first claim
        plus MAX_PREPUBLICATION_RETRIES further ones, never past the phase deadline."""
        doc = self._answer(await self._call("claim_preparation", {
            "job_id": job_id, "worker_id": worker_id,
            "limits": {"preparation_lease_ttl_s": self.limits.preparation_lease_ttl_s,
                       "max_prepublication_retries": self.limits.max_prepublication_retries}}))
        return Lease.model_validate(doc["lease"])

    async def prepared(self, lease: Lease, media: tuple[MediaRef, ...] = ()):
        """`preparing -> queued` fenced on the preparation lease, with its
        `inference_dispatch` event, in one transaction (the 06 `prepare` boundary)."""
        doc = self._answer(await self._call("prepare", {
            "lease": lease.model_dump(mode="json"),
            "media": [ref.model_dump(mode="json") for ref in media]}))
        return admission_of(doc) if doc["accounting_regime"] == "legacy_usd" \
            else admission_v2_of(doc)

    # --- the dispatch outbox (D2 item 2) -------------------------------------------
    async def dispatch_pending(self, *, limit: int = 100, worker_id: str = "relay",
                               redelivery_s: float = 30.0) -> tuple[IndexEvent, ...]:
        """Unacknowledged dispatch rows whose job still wants them, as index events."""
        docs = await self._call("dispatch_pending", {"limit": limit, "worker_id": worker_id,
                                                     "redelivery_s": redelivery_s})
        return tuple(IndexEvent.model_validate(doc) for doc in docs)

    async def acknowledge_dispatch(self, event_ids) -> int:
        """Delivery acknowledgment: the index now holds these candidates."""
        return await self._call("acknowledge_dispatch",
                                {"event_ids": [str(event_id) for event_id in event_ids]})

    async def dispatch_snapshot(self) -> tuple[IndexEvent, ...]:
        """PostgreSQL truth for `Scheduler.rebuild`."""
        rows = await self._query("select infrx.dispatch_snapshot()", ())
        return tuple(IndexEvent.model_validate(doc) for doc in rows[0][0])

    # --- D3 / D5 (fail closed) ---------------------------------------------------
    async def claim(self, job_id: str, worker_id: str):
        raise NotImplementedError("JobStore.claim is D3's")

    async def heartbeat(self, lease):
        raise NotImplementedError("JobStore.heartbeat is D3's")

    async def cancel(self, org_id: str, job_handle: str):
        raise NotImplementedError("JobStore.cancel is D3's")

    async def complete(self, lease, outcome):
        raise NotImplementedError("JobStore.complete is D5's")

    async def recover(self):
        raise NotImplementedError("JobStore.recover is D3's")
