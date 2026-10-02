"""J2: consented, budgeted submission and idempotent collection (JUDGE-BUDGET, JUDGE-SCORES).

`submit` is 02's judge workflow for a Lab provider judging a grantor's traces:

1. the mode is exactly `live` (R57), else nothing happens;
2. the **current** permission (L2 `authorize_content`, purpose `external_judging`, both content
   categories a judge reads) - its grant id and version are the run's consent snapshot;
3. the worst case at the rate effective on the store clock, reserved in PROVIDER_USD against a
   **named payer** of this provider (D6J; never a CREDIT wallet, never the consumer's money);
4. one submit intent (D6J `begin_submit`): only the call that created it may egress, so a
   double click or a restarted worker never sends a second batch;
5. the content, read through T3's `Retention` by durable request id with the grantor's
   organization bound - never the raw T2I projection, so a request its owner deleted or
   content past its bound is unreadable even before the sweep (WR-OBS-3, E5L-F1); a request
   with no readable content is skipped before egress, and the ids that do leave are
   recorded (D6J `record_sent`) so collection scores only what the judge actually saw;
6. the permission again, immediately before egress: a revocation since step 2 releases the
   hold and nothing leaves;
7. one provider call, never retried: a definite rejection releases, anything else (a timeout,
   a 5xx, a lost answer) quarantines the run `ambiguous` with the hold kept.

`reconcile` resolves `ambiguous` from provider evidence only (a lookup by the submit key),
never by submitting again; `collect` validates, deduplicates by run/sample/rubric version and
settles once. Egress goes only through a `JudgeProvider`; the one adapter (`http.py`) refuses
any host but the local judge fake until P-10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol, Sequence
from urllib.parse import urlsplit

import httpx

from ..contracts import errors
from ..contracts.lab.records import REF_RE
from ..contracts.limits import PilotSettings
from ..contracts.v2.money_units import ProviderUsd
from ..contracts.v2.records import DataCategory, DataPurpose
from .cost import JUDGE_MODE_LIVE, RateTable, TokenCeilings, worst_case
from .dryrun import DEFAULT_CEILINGS
from .rubric import MARLIN_VIDEO_V1, Result, Rubric, ScoreLedger, validate_json

PURPOSE = DataPurpose.external_judging
#: A judge reads the question and the answer; a grant for one of them is not enough.
CATEGORIES = (DataCategory.request_content, DataCategory.response_content)
#: Egress goes to the local judge fake only, until P-10 approves a provider and its rates.
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class SubmitRejected(Exception):
    """The provider definitely did not accept the batch (a 4xx before acceptance)."""


@dataclass(frozen=True)
class ConsentRef:
    """The grant a run was reserved under: audit evidence, never a standing permission."""

    grant_id: str
    version: int


@dataclass(frozen=True)
class LedgerRun:
    """D6J's run row. `state` is the frozen `external_run` machine (contracts/lab/states)."""

    run_id: str
    provider_org_id: str
    payer_ref: str
    consent: ConsentRef
    sample_ids: tuple[str, ...]
    media_ids: frozenset[str]
    price_version: str
    reserved: ProviderUsd
    state: str
    submit_key: str | None = None
    external_id: str | None = None
    actual: ProviderUsd | None = None
    sent_ids: tuple[str, ...] = ()           # the samples that left; a subset of sample_ids


@dataclass(frozen=True)
class ProviderResults:
    done: bool
    items: tuple[tuple[str, str], ...]        # (sample id, raw judge output)
    cost: ProviderUsd | None = None


class JudgeLedger(Protocol):
    """D6J (lab-sql, SR-J2-1): on PostgreSQL `infrx.state.lab_consent.PgJudgeLedger` (0036,
    WR-LSQ-4), gated by the `lab_submission` flag; `fakes.FakeJudgeLedger` in the unit cases."""

    async def db_now(self) -> datetime: ...

    async def reserve(self, *, run_id: str, provider_org_id: str, payer_ref: str,
                      consent: ConsentRef, sample_ids: Sequence[str], media_ids: frozenset[str],
                      price_version: str, max_cost: ProviderUsd) -> LedgerRun:
        """Atomic against the payer's budget, outstanding holds included; idempotent per run."""

    async def begin_submit(self, run_id: str) -> tuple[LedgerRun, bool]:
        """(run, True) only for the call that moved `prepared` -> `submitting`."""

    async def record_sent(self, run_id: str, sample_ids: Sequence[str]) -> LedgerRun:
        """The ids about to leave, recorded while `submitting`, before egress."""

    async def record_submission(self, run_id: str, external_id: str) -> LedgerRun: ...
    async def quarantine(self, run_id: str, reason: str) -> LedgerRun: ...
    async def release(self, run_id: str, state: str, reason: str) -> LedgerRun: ...
    async def record_results(self, run_id: str, results: Sequence[Result]) -> int: ...
    async def settle(self, run_id: str, actual: ProviderUsd) -> LedgerRun: ...
    async def run(self, run_id: str) -> LedgerRun | None: ...


class JudgeProvider(Protocol):
    async def submit(self, submit_key: str, items: list[dict[str, Any]]) -> str: ...
    async def lookup(self, submit_key: str) -> str | None: ...
    async def results(self, external_id: str) -> ProviderResults: ...


@dataclass(frozen=True)
class JudgeWiring:
    access: Any                  # infrx.lab.access.LabAccess
    ledger: JudgeLedger
    provider: JudgeProvider
    retention: Any               # T3 `Retention`: `read_content` (tombstones, content bound)
    rates: RateTable
    settings: PilotSettings


@dataclass(frozen=True)
class JudgeJob:
    run_id: str
    provider_org_id: str
    grantor_org_id: str
    model_id: str                # the grantor's model whose traces are judged
    judge_model: str             # the provider model that judges (the rate row)
    payer_ref: str
    request_ids: tuple[str, ...]
    media_ids: frozenset[str] = frozenset()
    rubric: Rubric = MARLIN_VIDEO_V1
    ceilings: TokenCeilings = field(default=DEFAULT_CEILINGS)


def require_own_payer(provider_org_id: str, payer_ref: object) -> None:
    """PROVIDER_USD work (a judge run, a teacher batch) is paid by this provider's named payer."""
    payer = REF_RE.fullmatch(payer_ref) if isinstance(payer_ref, str) else None
    if payer is None or payer.group(1) != "payer" or payer.group(2) != provider_org_id:
        raise errors.Forbidden("PROVIDER_USD work names this provider's own payer")


async def _permitted(job: JudgeJob, user_id: str, wiring: JudgeWiring) -> ConsentRef:
    for category in CATEGORIES:
        grant = await wiring.access.authorize_content(
            user_id=user_id, provider_org_id=job.provider_org_id,
            grantor_org_id=job.grantor_org_id, model_id=job.model_id, category=category,
            purpose=PURPOSE)
    return ConsentRef(grant.grant_id, grant.version)


async def submit(job: JudgeJob, *, user_id: str, wiring: JudgeWiring) -> LedgerRun:
    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:
        raise errors.BudgetExceeded("the judge is not in live mode: nothing is submitted")
    require_own_payer(job.provider_org_id, job.payer_ref)
    consent = await _permitted(job, user_id, wiring)
    ledger = wiring.ledger
    rate = wiring.rates.rate_for(job.judge_model, await ledger.db_now())
    if rate is None:
        raise errors.BudgetExceeded(f"{job.judge_model} has no approved rate: nothing reserved")
    run = await ledger.reserve(
        run_id=job.run_id, provider_org_id=job.provider_org_id, payer_ref=job.payer_ref,
        consent=consent, sample_ids=job.request_ids, media_ids=job.media_ids,
        price_version=rate.price_version,
        max_cost=ProviderUsd(worst_case(rate, job.ceilings, len(job.request_ids))))
    run, mine = await ledger.begin_submit(run.run_id)
    if not mine:
        return run               # another call holds (or held) the intent: never a second batch
    items = []
    for request_id in run.sample_ids:
        body = await wiring.retention.read_content(job.grantor_org_id, request_id)
        if body is not None:
            items.append({"sample_id": request_id, "content": body.decode()})
    return await send(ledger, wiring.provider, run, items,
                      lambda: _permitted(job, user_id, wiring))


async def send(ledger: JudgeLedger, provider: JudgeProvider, run: LedgerRun,
               items: list[dict[str, Any]], recheck: Callable[[], Awaitable[Any]]) -> LedgerRun:
    """Steps 5-7 for a run whose submit intent this call holds: the **one** egress path, shared
    by J2's judge runs and P2's teacher batches. `recheck` is the caller's current-permission
    check, awaited immediately before egress; `items` carry a `sample_id` each."""
    if not items:
        return await ledger.release(run.run_id, "failed", "no stored content left to judge")
    try:
        await recheck()
    except errors.DomainError:
        await ledger.release(run.run_id, "failed", "permission withdrawn before egress")
        raise
    run = await ledger.record_sent(run.run_id, [item["sample_id"] for item in items])
    try:
        external_id = await provider.submit(run.submit_key, items)
    except SubmitRejected as exc:
        return await ledger.release(run.run_id, "failed", f"provider rejected: {exc}"[:200])
    except Exception as exc:     # noqa: BLE001 - an unknown outcome is ambiguous, never retried
        return await ledger.quarantine(run.run_id, f"unknown submit outcome: "
                                                   f"{type(exc).__name__}")
    return await ledger.record_submission(run.run_id, external_id)


async def _run(ledger: JudgeLedger, run_id: str, state: str) -> LedgerRun:
    run = await ledger.run(run_id)
    if run is None or run.state != state:
        raise errors.StateConflict(f"judge run {run_id} is not {state}")
    return run


async def reconcile(run_id: str, *, wiring: JudgeWiring) -> LedgerRun:
    """`ambiguous` -> `submitted` on the provider's record of the submit key, or -> `failed`
    (hold released) when the provider has none. A lookup that fails leaves it ambiguous."""
    run = await _run(wiring.ledger, run_id, "ambiguous")
    external_id = await wiring.provider.lookup(run.submit_key)
    if external_id is None:
        return await wiring.ledger.release(run_id, "failed", "the provider has no such batch")
    return await wiring.ledger.record_submission(run_id, external_id)


async def collect(run_id: str, *, wiring: JudgeWiring,
                  rubric: Rubric = MARLIN_VIDEO_V1) -> LedgerRun:
    """Project what has arrived (once per run/sample/rubric version); settle when done."""
    run = await wiring.ledger.run(run_id)
    if run is not None and run.state == "completed":
        return run
    run = await _run(wiring.ledger, run_id, "submitted")
    polled = await wiring.provider.results(run.external_id)
    plan = ScoreLedger(run.run_id, rubric.version, run.sent_ids)
    for sample_id, text in polled.items:
        plan.deliver(validate_json(rubric, text, run_id=run.run_id, sample_id=sample_id,
                                   media_available=sample_id in run.media_ids))
    await wiring.ledger.record_results(run.run_id, plan.results)
    if not polled.done:
        return run
    return await wiring.ledger.settle(run.run_id, polled.cost)


def egress_hosts(mode: str, allowlist: str) -> frozenset[str]:
    """AP-08 P-10 seam: the operator-approved judge provider hosts (`JUDGE_PROVIDER_ALLOWLIST`,
    comma-separated bare host names), honoured only when the mode is exactly `live` (R57);
    any other mode, or no list, keeps egress on loopback. Approving a host is the operator's
    P-10 decision, never a default."""
    if mode != JUDGE_MODE_LIVE:
        return frozenset()
    hosts = frozenset(h.strip().lower() for h in allowlist.split(",") if h.strip())
    if any(not h.replace("-", "").replace(".", "").isalnum() for h in hosts):
        raise errors.InvalidRequest("JUDGE_PROVIDER_ALLOWLIST names bare host names")
    return hosts


class HttpJudgeProvider:
    """`JudgeProvider` over HTTP, to the local judge fake only (P-10 gates anything else).

    One request per call and no retry here (httpx retries nothing by default): a retried
    create after an ambiguous timeout is the second billable batch 02 forbids. Redirects are
    not followed (httpx's default) and no proxy is taken from the environment: either would
    carry the payload somewhere other than the host this constructor checked. A 2xx other
    than 200, a 3xx or a 5xx may have been accepted, so it is ambiguous, never a failure.
    """

    def __init__(self, base_url: str, *, timeout_s: float = 10.0,
                 allowed_hosts: frozenset[str] = frozenset()) -> None:
        parts = urlsplit(base_url)
        approved = parts.scheme == "https" and parts.hostname in allowed_hosts
        if not approved and (parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS):
            raise errors.InvalidRequest("judge egress is limited to the local judge fake "
                                        "until P-10")
        self.base_url, self.timeout_s = base_url.rstrip("/"), timeout_s

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s,
                                 trust_env=False)

    async def submit(self, submit_key: str, items: list[dict[str, Any]]) -> str:
        async with self._client() as client:
            response = await client.post("/batches", json={"submit_key": submit_key,
                                                            "items": items})
        if 400 <= response.status_code < 500:
            raise SubmitRejected(f"HTTP {response.status_code}")
        if response.status_code != 200:
            raise errors.AmbiguousSubmission(f"HTTP {response.status_code}: outcome unknown")
        return response.json()["id"]

    async def lookup(self, submit_key: str) -> str | None:
        async with self._client() as client:
            response = await client.get("/batches", params={"submit_key": submit_key})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["id"]

    async def results(self, external_id: str) -> ProviderResults:
        async with self._client() as client:
            response = await client.get(f"/batches/{external_id}")
        response.raise_for_status()
        body = response.json()
        return ProviderResults(done=body["done"],
                               items=tuple((sid, text) for sid, text in body["items"]),
                               cost=ProviderUsd(body["cost"]) if body["done"] else None)
