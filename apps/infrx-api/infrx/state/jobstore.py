"""D2: the PostgreSQL `JobStore` - admission, preparation and the dispatch outbox.

Every operation is ONE call of one SECURITY DEFINER function (the 06 mutation boundary),
so each is one transaction on the database clock (R7); this module only shapes records
and maps refusals. The function bodies live in `apps/app/supabase/migrations/0011_*`
onward; `credit_schema.py` records their names and the lock order.

Refusals raised in SQL arrive as SQLSTATE P0001 with the message `<error_code>: detail`
and become the `DomainError` of that code. D1R's seams keep their own SQLSTATEs.

D3 (0016) adds the fenced leases: `claim`, `heartbeat`, `load_work`, `cancel`, `recover`
and the fence of `complete`, whose settlement (D5) still raises `NotImplementedError`: a
fail-closed boundary, never a silent success.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..contracts import errors, ids
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import (Admission, Budgets, IdempotencyRef, IndexEvent, Lease,
                                 MediaRef, NormalizedRequest, ReservationKind, TerminalCause,
                                 TerminalOutcome, Work)
from ..contracts.v2.records import AdmissionV2

#: `async () -> psycopg.AsyncConnection` in autocommit, acting as `service_role`.
Connect = Callable[[], Awaitable[Any]]

#: The admission fields the port's `Admission` record carries (the SQL document has more).
_ADMISSION_FIELDS = tuple(name for name in Admission.model_fields if name != "schema_version")
#: How long an acknowledged outbox row is kept before `gc_outbox` may delete it.
#: ponytail: a constant (7 days, the processing-cache horizon); a `PilotSettings` field when
#: an operator needs to tune it.
OUTBOX_RETENTION_S = 7 * 86_400.0
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
    """code -> the MOST specific class that declares it (`StateConflict`, not its base
    `Conflict`, which shares the code), so `except errors.StateConflict` works."""
    found: dict[str, type[errors.DomainError]] = {}

    def walk(cls, depth: int) -> None:
        if "code" in cls.__dict__ and depth >= found.get(cls.code, (None, -1))[1]:
            found[cls.code] = (cls, depth)
        for sub in cls.__subclasses__():
            walk(sub, depth + 1)
    walk(errors.DomainError, 0)
    return {code: cls for code, (cls, _) in found.items()}


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


class PreparedWork(Work):
    """`Work` plus preparation's exact prompt count (`prepared(..., prompt_tokens=)`, W2
    request 3), None before preparation stored one. A local attribute until the F2P wire-in
    adds `Work.prompt_tokens` to the contract; then this class goes."""

    prompt_tokens: int | None = None


class PgJobStore:
    """`ports.JobStore` over PostgreSQL. `limits` is the store's own configuration: caps,
    budgets and ceilings come from here, never from a caller (R4, R53)."""

    def __init__(self, connect: Connect, *, limits: PilotSettings = DEFAULTS) -> None:
        self._connect = connect
        self.limits = limits
        #: The last `recover` sweep's jobs it could not reap: job id -> "sqlstate: detail".
        self.unsettleable: dict[str, str] = {}

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

    async def prepared(self, lease: Lease, media: tuple[MediaRef, ...] = (), *,
                       prompt_tokens: int | None = None):
        """`preparing -> queued` fenced on the preparation lease, with its
        `inference_dispatch` event, in one transaction (the 06 `prepare` boundary).

        `prompt_tokens` (W2's request) is preparation's exact count, stored once with the
        refs and refused past the job's `max_input_tokens`; it reaches `Work` when the
        coordinator adds `Work.prompt_tokens` (integration request)."""
        if prompt_tokens is not None and (isinstance(prompt_tokens, bool)
                                          or not isinstance(prompt_tokens, int)):
            raise errors.InvalidRequest("prompt_tokens must be an integer")
        doc = self._answer(await self._call("prepare", {
            "lease": lease.model_dump(mode="json"),
            "media": [ref.model_dump(mode="json") for ref in media],
            "prompt_tokens": prompt_tokens}))
        return admission_of(doc) if doc["accounting_regime"] == "legacy_usd" \
            else admission_v2_of(doc)

    # --- the dispatch outbox (D2 item 2) -------------------------------------------
    async def dispatch_pending(self, *, worker_id: str, limit: int = 100,
                               redelivery_s: float = 30.0) -> tuple[IndexEvent, ...]:
        """Unacknowledged dispatch rows whose job still wants them, as index events, claimed
        for `worker_id` (required, unique per relay: only it may acknowledge them)."""
        docs = await self._call("dispatch_pending", {"limit": limit, "worker_id": worker_id,
                                                     "redelivery_s": redelivery_s})
        return tuple(IndexEvent.model_validate(doc) for doc in docs)

    async def acknowledge_dispatch(self, event_ids, *, worker_id: str) -> int:
        """Delivery acknowledgment: the index now holds these candidates. Only the claim
        holder's acknowledgment lands (OB-1b); a reopened or released row answers 0."""
        return await self._call("acknowledge_dispatch", {
            "event_ids": [str(event_id) for event_id in event_ids], "worker_id": worker_id})

    async def db_now(self):
        """The store clock (`infrx.now()`), e.g. the lower bound of a rebuild fence."""
        return (await self._query("select infrx.now()", ()))[0][0]

    async def reopen_dispatch(self, since) -> int:
        """The rebuild fence (0012): dispatch rows acknowledged/claimed at or after
        `since` whose job still wants them become pending again."""
        return await self._call("reopen_dispatch", {"since": since.isoformat()})

    async def release_dispatch(self, event_ids) -> int:
        """Hand read-but-unindexed rows back for the next pump now (OB-4)."""
        return await self._call("release_dispatch",
                                {"event_ids": [str(event_id) for event_id in event_ids]})

    async def record_dispatch_error(self, event_id, error: str) -> int:
        """The index refused a row for a reason other than capacity (OB-7)."""
        return await self._call("fail_dispatch", {"event_id": str(event_id), "error": error})

    async def dispatch_snapshot(self) -> tuple[IndexEvent, ...]:
        """PostgreSQL truth for `Scheduler.rebuild`."""
        rows = await self._query("select infrx.dispatch_snapshot()", ())
        return tuple(IndexEvent.model_validate(doc) for doc in rows[0][0])

    async def gc_outbox(self, *, retention_s: float = OUTBOX_RETENTION_S,
                        limit: int = 1000) -> dict[str, int]:
        """Expire dispatch rows of terminal jobs; delete acknowledged rows past
        `retention_s` that no live job, tombstone or consumer can still need (0013).
        The tombstone is the store's own idempotency TTL. Bounded."""
        return await self._call("gc_outbox", {
            "retention_s": retention_s, "tombstone_s": self.limits.idempotency_ttl_s,
            "limit": limit})

    # --- W2 / M3 requests (D2 item 4) --------------------------------------------
    async def is_live(self, job_id: str) -> bool:
        """M3's collector: True while the job is not terminal; an unknown (or malformed)
        id is not live."""
        if not ids.is_request_id(job_id):
            return False
        rows = await self._query("select settled_at is null from infrx.jobs "
                                 "where request_id = %s", (job_id,))
        return bool(rows and rows[0][0])

    async def put_result(self, job_id: str, text: str) -> str:
        """W2's result object writer (02 §7): immutable, first write wins; returns the
        `infrx-result:<job_id>` reference `complete` carries (R30)."""
        return await self._call("put_result", {"job_id": job_id, "text": text})

    async def read_result(self, org_id: str, result_ref: str) -> str:
        rows = None
        try:
            rows = await self._query("select infrx.read_result(%s, %s)", (org_id, result_ref))
        except Exception as failed:                  # psycopg.Error, mapped
            raise domain_error(failed) from None
        return rows[0][0]

    # --- D3: fenced leases, recovery and cancellation (0016) ------------------------
    def _lease_limits(self) -> dict[str, Any]:
        """The store's own lease configuration, sent with every D3 call (never a caller's)."""
        return {name: getattr(self.limits, name) for name in (
            "lease_ttl_s", "preparation_lease_ttl_s", "max_prepublication_retries",
            "unknown_usage_reconcile_s")}

    async def _fenced(self, function: str, lease: Lease, **extra: Any) -> dict:
        """One fenced boundary call: the lease is a fencing token, the refusal after an R29
        terminalization comes back as data (R39) and is raised here."""
        return self._answer(await self._call(function, {
            "lease": lease.model_dump(mode="json"), "limits": self._lease_limits(), **extra}))

    async def claim(self, job_id: str, worker_id: str) -> Lease:
        """`queued -> running`, the next inference generation, on the database clock."""
        doc = await self._call("claim", {"job_id": job_id, "worker_id": worker_id,
                                         "limits": self._lease_limits()})
        return Lease.model_validate(doc["lease"])

    async def heartbeat(self, lease: Lease) -> Lease:
        """Renews the STORED lease (R29); a preparation lease never past its phase (R52)."""
        return Lease.model_validate((await self._fenced("heartbeat", lease))["lease"])

    async def load_work(self, lease: Lease) -> PreparedWork:
        """R46: fenced like a mutation. A CREDIT job's work is a `WorkV2` this v1 port cannot
        carry (no price snapshot), so it is refused rather than invented. Interim until
        WorkV2: SQL `claim` never leases a CREDIT job (MY-3), so only a preparation lease
        reaches this refusal."""
        doc = await self._fenced("load_work", lease)
        admission = doc["admission"]
        if admission["accounting_regime"] != "legacy_usd":
            raise errors.InvalidRequest(f"job {lease.job_id} is a CREDIT job: its work is "
                                        f"WorkV2, which the v1 load_work cannot carry")
        request = NormalizedRequest.model_validate(doc["request"])
        return PreparedWork(request=request, media_refs=request.media,
                            prepared_refs=tuple(MediaRef.model_validate(r)
                                                for r in doc["prepared_refs"]),
                            price_snapshot=admission["price_snapshot"],
                            budgets=admission["budgets"],
                            prompt_tokens=admission["prepared_prompt_tokens"])

    async def cancel(self, org_id: str, job_handle: str, *,
                     cause: TerminalCause = TerminalCause.client_cancelled) -> TerminalOutcome:
        """Terminalizes and releases the hold and capacity in one transaction; a job that is
        already terminal answers its committed outcome (completion won).

        Until D5's 0018, `infrx.cancel` (0016) records `client_cancelled` whatever it is
        sent, so every other cause is refused HERE, before any SQL, with
        `UnsupportedParameter` (an `InvalidRequest`, `param="cause"`): nothing is written and
        nothing records the wrong cause. D5 removes the refusal and sends `cause`."""
        if cause != TerminalCause.client_cancelled:
            raise errors.UnsupportedParameter(f"cause {cause!r} needs migration 0018 (D5)",
                                              param="cause")
        return _outcome(await self._call("cancel", {"org_id": org_id, "job_handle": job_handle,
                                                    "limits": self._lease_limits()}))

    async def complete(self, lease: Lease, outcome: TerminalOutcome) -> TerminalOutcome:
        """D3 runs the fence (stale/foreign/expired/wrong-kind refused, R29 terminalizes);
        the settlement after it is D5's, so a lease that holds still fails closed."""
        from psycopg.errors import FeatureNotSupported
        try:
            await self._fenced("terminalize", lease, outcome=outcome.model_dump(mode="json"))
        except FeatureNotSupported:
            pass
        raise NotImplementedError("JobStore.complete: the fence held; the settlement is D5's")

    async def recover(self) -> tuple[TerminalOutcome | IndexEvent, ...]:
        """The reaper, on the database clock (R7). Requeues become outbox dispatch rows (the
        relay delivers them) and are also returned as `IndexEvent`s with the same ids. A job
        the sweep could not reap is reported in `self.unsettleable`, never returned."""
        produced: list[TerminalOutcome | IndexEvent] = []
        self.unsettleable = {}
        for item in await self._call("recover", {"limits": self._lease_limits()}):
            if "outcome" in item:
                produced.append(_outcome(item["outcome"]))
            elif "index_event" in item:
                produced.append(IndexEvent.model_validate(item["index_event"]))
            else:
                failed = item["unsettleable"]
                self.unsettleable[failed["job_id"]] = f"{failed['code']}: {failed['detail']}"
        return tuple(produced)
