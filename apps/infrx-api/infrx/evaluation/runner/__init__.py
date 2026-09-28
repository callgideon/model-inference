"""B1: durable offline evaluation runs (EVAL-DURABLE, EVAL-REPRO).

**B1.a `freeze`.** A run is the F3 record `lab.eval_run.1`: dataset, harness, serving and
evaluator refs, seed, `max_cases` and budgets, published through D7 (content-addressed, so
the same record is the same `run_ref`) and created there (`lab_create_run`: the scheduling
gate refuses a sample whose grant is not current, and the cases are the first `max_cases`
sample ids, R168). The evaluator's ref digest is the sha256 of its spec's canonical JSON
(`evaluator_ref`): a spec that is not the one the run names is refused, and the spec is
registered in D7 (`put_evaluator`, 0034 R167) before the run is published, since D7 refuses
a run or a result under an unregistered evaluator. The spec holds the metric, the reference path into the case and H1's
per-case replay bounds. A run spends provider_dev CREDIT, so it must carry a CREDIT budget.
`Frozen` also carries the dataset's `split_digest` and its holdout cases (WR-N-4: D7 has
just refused scheduling unless every sample's grant is current for provider_sharing, so at
freeze the holdout is all accessible; each attempt re-reads the gate). The dataset ref pins
both, being the digest of the manifest.

**`resume`** rebuilds the same `Frozen` for a worker's delivery from the created run (D7's
run status names its `run_ref`), with no scheduling gate: after a revocation `freeze` is
refused, but the run's cases must still end, each attempt failing as `revoked`.

**B1.b `Runner.run`.** `concurrency` workers loop over D7's leases: each lease is one
attempt of one case (`attempt:<run>:<case>:<n>`). Before leasing, the run's recorded CREDIT
is checked against its budget. Per attempt: past `max_attempts` the case fails without a
call (a poison case cannot loop); the access gate is read again (a revoked case fails as
`revoked`, never sent); then H1's `Replayer` runs the harness against the dev deployment,
each model call fenced first by a heartbeat (a cancel or a lost lease stops spending at
once) and keyed `<attempt key>:<call n>`. A retryable refusal (429/5xx/transport) is sent
again under the same key, so the endpoint replays instead of charging twice, at most
`dispatch_retries` times. A worker that dies leaves its lease to expire; `lab_recover`
puts the case back and the next attempt is a new, bounded, paid attempt. An exhausted dev
wallet (402) stops the whole run for good: unfinished cases stay pending/leased
(unresolved), never dropped. A 402 is the wallet's, not the case's: its attempt is given
back (`store.release`, WR-B-2(d)) so it never counts toward `max_attempts`, and the re-run
is the same attempt under the same key (calls already charged replay, not re-charged).

**B1.c results.** A harness outcome is the case's one result under the run's evaluator
(`complete`/`failed` are scored by the metric; `blocked`/`unsupported` are recorded with
no score), with its split, usage, latency and a cost per attempt (the dev rate card's
debit). Infrastructure failures (retries spent, a final refusal, a replay bound, revoked
data, attempts spent) fail the attempt with no result and their cost. A finish refused by
the fence (cancelled, expired, taken over) is dropped: no stale result overwrites a live
one. D7 keeps one result per run x case x evaluator.

**Who schedules (WR-B-4, R160's membership half).** `freeze` takes the provider from the
L2 port: `user_id` must hold a current membership of `provider_org_id` whose role carries
`run_evaluation`, read on the store's clock at the call (no membership, a revoked one or
another provider's is `NotFound`; a viewer is `Forbidden`); D7 then checks the grant half.
The worker entry point that runs `resume`/`Runner.run` for an `eval_run` outbox event is
wiring (WR-B-5).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field, ValidationError

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2 import records as v2
from ...contracts.v2.money_units import Credit
from ...contracts.v2.records import ProviderCapability
from ...datasets.imports import media_key, sample_key
from ...datasets.versions import split_digest
from ...harnesses.replay import Bounds, Replayer, ReplayBoundExceeded
from ...lab.access import LabAccess

MAX_BODY = 65536                        # D7's bound on one result body
RETRYABLE = (errors.RateLimitError, errors.ServerError)
_MISSING = object()


def _at(case: dict[str, Any], path: str) -> Any:
    """The value at a dotted path into the case, or `_MISSING`."""
    for part in path.split("."):
        case = case.get(part, _MISSING) if isinstance(case, dict) else _MISSING
    return case


class EvaluatorSpec(lab.LabModel):
    metric: Literal["exact_match"]
    reference: lab.Text                 # a dotted path into the case, e.g. sample.original.answer
    max_requests: int = Field(ge=1)
    max_bytes: int = Field(ge=1)
    max_seconds: int = Field(ge=1)


def evaluator_ref(spec: dict[str, Any], *, provider_org_id: str, evaluator_id: str) -> str:
    digest = hashlib.sha256(lab.canonical(spec)).hexdigest()
    return f"lab:evaluator:{provider_org_id}:{evaluator_id}@sha256:{digest}"


def case_of(provider_org_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """The case H1 replays from N1's content object. A finite-video clip (N1 stores
    `media_digest` and `span_ms`) also carries what H1's `finite_video` adapter reads:
    `sample.media_ref`, the clip's media key, and `sample.duration_ms`, the span (E6L-O1)."""
    if "media_digest" in content:
        start, end = content["span_ms"]
        content = {**content, "media_ref": media_key(provider_org_id, content["media_digest"]),
                   "duration_ms": end - start}
    return {"sample": content}


@dataclass(frozen=True)
class Frozen:
    run: lab.EvalRun
    run_ref: str
    manifest: lab.DatasetManifest
    harness: dict[str, Any]
    evaluator: dict[str, Any]
    split_digest: str
    cases: tuple[str, ...]
    holdout: tuple[str, ...]
    limit: Credit


def _checked(run: lab.LabRecord, evaluator: dict[str, Any]) -> Credit:
    """The run's CREDIT limit, once it is a run and `evaluator` is the spec it names."""
    if not isinstance(run, lab.EvalRun):
        raise errors.InvalidRequest("not an evaluation run")
    evaluator_id = lab.REF_RE.fullmatch(run.evaluator_ref).group(3)
    if evaluator_ref(evaluator, provider_org_id=run.provider_org_id,
                     evaluator_id=evaluator_id) != run.evaluator_ref:
        raise errors.InvalidRequest("the evaluator spec is not the one the run's evaluator "
                                    "ref names")
    try:
        EvaluatorSpec.model_validate(evaluator)
    except ValidationError as refused:
        raise errors.InvalidRequest(f"evaluator spec: {refused}") from None
    limits = [b.limit for b in run.budgets if b.limit.unit == "CREDIT"]
    if not limits:
        raise errors.InvalidRequest("a run spends provider_dev CREDIT under a CREDIT budget")
    return limits[0].amount


async def _pinned(store, run: lab.EvalRun, run_ref: str, evaluator: dict[str, Any],
                  limit: Credit) -> Frozen:
    manifest = await store.resolve(run.dataset_ref, provider_org_id=run.provider_org_id)
    harness = await store.resolve(run.harness_ref, provider_org_id=run.provider_org_id)
    cases = tuple(sorted(s.sample_id for s in manifest.samples)[:run.max_cases])
    return Frozen(run=run, run_ref=run_ref, manifest=manifest,
                  harness=harness.model_dump(by_alias=True, exclude_unset=True),
                  evaluator=evaluator, split_digest=split_digest(manifest.splits.model_dump()),
                  cases=cases,
                  holdout=tuple(c for c in cases if c in manifest.splits.holdout),
                  limit=limit)


async def may_schedule(access: LabAccess, *, user_id: str, provider_org_id: str) -> None:
    """WR-B-4: a current member of the provider whose role runs evaluations."""
    membership = await access.store.membership(provider_org_id, user_id)
    now = await access.store.db_now()
    if membership is None or not membership.permits(
            ProviderCapability.read_aggregate_health, now, provider_org_id):
        raise errors.NotFound("no such provider workspace")
    if not membership.permits(ProviderCapability.run_evaluation, now, provider_org_id):
        raise errors.Forbidden("this provider role does not run evaluations")


async def freeze(store, payload: dict[str, Any], *, evaluator: dict[str, Any],
                 access: LabAccess, user_id: str, provider_org_id: str) -> Frozen:
    """The run published and created in D7 for a member allowed to schedule it; the same
    payload is the same run."""
    run = lab.parse(payload)
    limit = _checked(run, evaluator)
    await may_schedule(access, user_id=user_id, provider_org_id=provider_org_id)
    await store.put_evaluator(evaluator, provider_org_id=provider_org_id, actor=user_id,
                              evaluator_id=lab.REF_RE.fullmatch(run.evaluator_ref).group(3))
    run_ref = await store.publish(payload, provider_org_id=provider_org_id, actor=user_id)
    await store.create_run(run_ref, provider_org_id=provider_org_id)
    return await _pinned(store, run, run_ref, evaluator, limit)


async def resume(store, run_id: str, *, evaluator: dict[str, Any],
                 provider_org_id: str) -> Frozen:
    """The created run as `freeze` pinned it, rebuilt from D7 without the scheduling gate."""
    run_ref = (await store.run_status(run_id, provider_org_id=provider_org_id))["run_ref"]
    run = await store.resolve(run_ref, provider_org_id=provider_org_id)
    return await _pinned(store, run, run_ref, evaluator, _checked(run, evaluator))


@dataclass(frozen=True)
class Limits:
    lease_s: float
    max_attempts: int
    dispatch_retries: int
    concurrency: int


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    charged: Credit


class DevEndpoint(Protocol):
    """One call to the run's dev deployment, paid from the provider_dev wallet. Raises
    `InsufficientCredit` (402), a `RETRYABLE` error, or any other `DomainError` (final)."""

    async def complete(self, *, prompt: str, media: list[str],
                       tool_results: list[dict[str, Any]], seed: int,
                       idempotency_key: str) -> Completion: ...


@dataclass
class _Bill:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    charged: Credit = Credit("0")


class Runner:
    def __init__(self, store, objects, endpoint: DevEndpoint,
                 deployment: v2.DeploymentRevision, *, worker_id: str, limits: Limits,
                 recordings: dict[str, Any] | None = None) -> None:
        self._store, self._objects, self._endpoint = store, objects, endpoint
        self._deployment, self._worker, self._limits = deployment, worker_id, limits
        self._recordings = recordings or {}

    async def run(self, frozen: Frozen) -> dict[str, Any]:
        """Work the run until no case is left to lease, it is cancelled or finished, or a
        stop (budget, wallet); answer D7's status plus what this delivery saw."""
        self._frozen, self._stop = frozen, None
        self._failures: dict[str, str] = {}
        self._abandoned: list[str] = []
        self._unrecorded = Credit("0")
        loop = asyncio.get_running_loop()
        await asyncio.gather(*(self._work(f"{self._worker}/{i}", loop)
                               for i in range(self._limits.concurrency)))
        status = await self._store.run_status(frozen.run.run_id,
                                              provider_org_id=frozen.run.provider_org_id)
        stopped = self._stop or ("cancelled" if status["state"] == "cancelled" else None)
        return {**status, "stopped": stopped, "failures": self._failures,
                "abandoned": sorted(self._abandoned),
                "unrecorded": {"CREDIT": str(self._unrecorded)}}

    async def _work(self, worker_id: str, loop) -> None:
        run = self._frozen.run
        while self._stop is None:
            status = await self._store.run_status(run.run_id, provider_org_id=run.provider_org_id)
            # ponytail: in-flight attempts may overshoot by one attempt per worker. B-R8: a
            # charge this delivery could not record still counts.
            if Credit(status["costs"].get("CREDIT", "0")) + self._unrecorded \
                    >= self._frozen.limit:
                self._stop = "budget_exhausted"
                return
            try:
                lease = await self._store.lease_case(
                    run.run_id, provider_org_id=run.provider_org_id, worker_id=worker_id,
                    lease_s=self._limits.lease_s)
            except errors.AlreadyTerminal:
                return
            if lease is None:
                return
            await self._attempt(lease, loop)

    async def _attempt(self, lease: dict[str, Any], loop) -> None:
        f, case_id = self._frozen, lease["case_id"]
        if lease["attempt"] > self._limits.max_attempts:
            return await self._finish(lease, "failed", reason="attempts_exhausted")
        if case_id not in await self._store.accessible_samples(
                f.run.dataset_ref, provider_org_id=f.run.provider_org_id,
                purpose="provider_sharing"):
            return await self._finish(lease, "failed", reason="revoked")
        sample = next(s for s in f.manifest.samples if s.sample_id == case_id)
        spec, bill, started = f.evaluator, _Bill(), time.monotonic()
        try:
            raw = await self._objects.get(sample_key(f.run.provider_org_id,
                                                     sample.content_digest))
            if raw is None:
                return await self._finish(lease, "failed", reason="missing_content")
            case = case_of(f.run.provider_org_id, json.loads(raw))
            replayer = Replayer(f.harness, self._deployment, self._bridge(lease, bill, loop),
                                self._recordings, Bounds(spec["max_requests"],
                                                         spec["max_bytes"], spec["max_seconds"]))
            outcome = await asyncio.to_thread(replayer.replay, case)
        except errors.InsufficientCredit:
            self._stop = "wallet_exhausted"
            return await self._release(lease, bill)
        except errors.StaleLease:
            return self._abandon(case_id, bill)
        except ReplayBoundExceeded as bound:
            return await self._finish(lease, "failed", reason=f"bound:{bound.bound}", bill=bill)
        except errors.DomainError as refused:
            return await self._finish(lease, "failed", reason=refused.code, bill=bill)
        except Exception as broken:        # B-R7: this case's, never the whole delivery's
            return await self._finish(lease, "failed", reason=f"error:{type(broken).__name__}",
                                      bill=bill)
        reference = _at(case, spec["reference"])
        reasons = list(outcome.reasons)
        if reference is _MISSING:
            reasons.append("missing_reference")
        body = {"status": outcome.status, "comparable": outcome.comparable, "reasons": reasons,
                "output": outcome.output,
                "score": float(outcome.output == reference)
                if outcome.comparable and reference is not _MISSING else None,
                "split": next(name for name in ("train", "validation", "holdout")
                              if case_id in getattr(f.manifest.splits, name)),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "usage": {"calls": bill.calls, "prompt_tokens": bill.prompt_tokens,
                          "completion_tokens": bill.completion_tokens}}
        text = lab.canonical(body).decode()
        if len(text) > MAX_BODY:
            body.update(output=None, reasons=[*reasons, "output_too_large"])
            text = lab.canonical(body).decode()
        await self._finish(lease, "succeeded", bill=bill,
                           results=[{"evaluator_ref": f.run.evaluator_ref, "body": text}])

    def _bridge(self, lease, bill: _Bill, loop):
        """H1's synchronous model port, run on the Replayer's thread, into this loop."""
        def model(prompt, media, results):
            return asyncio.run_coroutine_threadsafe(
                self._call(lease, bill, prompt, media, results), loop).result()
        return model

    async def _call(self, lease, bill: _Bill, prompt: str, media: list[str],
                    results: list[dict[str, Any]]) -> dict[str, Any]:
        await self._store.heartbeat(lease, lease_s=self._limits.lease_s)   # the fence
        key = f"{lease['idempotency_key']}:{bill.calls}"
        bill.calls += 1
        # ponytail: immediate retries; honour Retry-After when the real L3 endpoint sends it.
        for left in range(self._limits.dispatch_retries, -1, -1):
            try:
                done = await self._endpoint.complete(prompt=prompt, media=media,
                                                     tool_results=results,
                                                     seed=self._frozen.run.seed,
                                                     idempotency_key=key)
                break
            except RETRYABLE:
                if not left:
                    raise
        bill.prompt_tokens += done.prompt_tokens
        bill.completion_tokens += done.completion_tokens
        bill.charged += done.charged
        return {"text": done.text, "tool_calls": done.tool_calls}

    async def _finish(self, lease, outcome: str, *, reason: str | None = None,
                      results: list[dict[str, str]] | None = None,
                      bill: _Bill | None = None) -> None:
        cost = None if bill is None else {"unit": "CREDIT", "value": str(bill.charged)}
        try:
            await self._store.finish(lease, outcome=outcome, results=results or [], cost=cost)
        except errors.StaleLease:
            return self._abandon(lease["case_id"], bill)
        if reason:
            self._failures[lease["case_id"]] = reason

    async def _release(self, lease, bill: _Bill) -> None:
        """Give a 402's attempt back: the case is pending with the attempt uncounted."""
        try:
            await self._store.release(lease)            # 0034 lab_release_attempt (B-R1)
        except errors.StaleLease:
            pass
        self._abandon(lease["case_id"], bill)

    def _abandon(self, case_id: str, bill: _Bill | None) -> None:
        self._abandoned.append(case_id)
        if bill is not None:
            self._unrecorded += bill.charged


class HttpDevEndpoint:
    """The L3 stand-in: an OpenAI-compatible `/v1/chat/completions` of one private dev
    deployment, called with its endpoint-scoped provider_dev credential. The gateway meters
    the call (D5 settles `rate_card.debit(usage)` into the dev wallet); the attempt's cost is
    the same debit on the rate card frozen at the deployment's publication."""

    def __init__(self, base_url: str, *, api_key: str, model: str,
                 rate_card: v2.RateCardSnapshot, client: httpx.AsyncClient | None = None) -> None:
        self._url, self._key, self._model = f"{base_url}/v1/chat/completions", api_key, model
        self._rate_card = rate_card
        self._client = client or httpx.AsyncClient(timeout=300)

    async def complete(self, *, prompt: str, media: list[str],
                       tool_results: list[dict[str, Any]], seed: int,
                       idempotency_key: str) -> Completion:
        if media:
            raise errors.InvalidRequest("finite video dispatch waits on L3's media path")
        if tool_results:        # B-R3: the gateway takes system/user/assistant, no tools
            raise errors.InvalidRequest("tool replay waits on the gateway's tool support")
        messages = [{"role": "user", "content": prompt}]
        try:
            answer = await self._client.post(
                self._url, json={"model": self._model, "seed": seed, "messages": messages},
                headers={"Authorization": f"Bearer {self._key}",
                         "Idempotency-Key": idempotency_key})
        except httpx.TransportError:
            raise errors.DependencyUnavailable("the dev endpoint did not answer") from None
        if answer.status_code == 402:
            raise errors.InsufficientCredit("the provider_dev wallet is exhausted")
        if answer.status_code == 429 or answer.status_code >= 500:
            raise errors.DependencyUnavailable(f"dev endpoint {answer.status_code}")
        if answer.status_code != 200:
            raise errors.InvalidRequest(f"dev endpoint refused: {answer.status_code}")
        body = answer.json()
        message, usage = body["choices"][0]["message"], body["usage"]
        calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
                 for c in message.get("tool_calls") or []]
        return Completion(text=message.get("content") or "", tool_calls=calls,
                          prompt_tokens=usage["prompt_tokens"],
                          completion_tokens=usage["completion_tokens"],
                          charged=self._rate_card.debit(usage["prompt_tokens"],
                                                        usage["completion_tokens"]))
