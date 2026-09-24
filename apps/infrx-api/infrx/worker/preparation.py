"""PREP-WORKER (I2B-R5): the product preparation loop - `prepare_dispatch` to `prepared`.

    runner = PreparationRunner(jobs=store, media=media, engine=engine, worker_id="worker-a")
    loop = WorkerLoop(scheduler=index, runner=runner, worker_id="worker-a",
                      kind=OutboxKind.prepare_dispatch, limits=limits)

`python -m infrx.worker` runs this pool beside the inference pool (W3's `WorkerService`,
`PREPARATION_CONCURRENCY` runners). One attempt per candidate:

1. `claim_preparation(job_id, worker)`: the fenced preparation lease (R46/R52). A lost claim
   (another worker holds it, the job moved on or ended) is reported, never retried here.
2. `load_work(lease)`: the preparation read path. The store's fenced read takes a
   preparation lease as well as an inference one (0016/0018 `fence_lease(...,
   ['preparation', 'inference'])`, the fake's `_fence_for_work`); in the CREDIT regime the
   worker's `jobs` is `CreditWork`, so this is `load_work_credit`.
3. Media, only when the request carries any: wait (bounded) for the admitting gateway's
   durable attach, which lands just after the admission commits (R99 (c)), then M's
   `prepare(job_id, profile)` over the object store and the shared processing cache.
4. **The count, exactly as the engine counts it**: vLLM's `POST /tokenize` on the chat body
   `VllmEngine.upstream_body` would send (the chat template; for a video, the local file and
   the pinned `mm_processor_kwargs`). The answer is checked, never replaced or estimated: an
   integer `count` agreeing with its `tokens`, and for a video between one and 196
   `video_token_id`s per two-frame patch of the pinned budget (`models/marlin2b/tokens.py`,
   marlin-sop.md §1.5) - one placeholder is a tokenizer that skipped the multimodal
   processor, and more than the budget is an engine not running the pinned profile. No
   answer within `PREPARATION_TIMEOUT_S`, or one that fails a check, is
   `dependency_unavailable`.
5. `prepared(lease, refs, prompt_tokens=count)`.

**A typed refusal anywhere prepares nothing** (a fetch or stage `not_found`, an
`unsupported_media`, the tokenizer's `dependency_unavailable`, a count past the job's
ceiling): the lease is left to lapse, `recover` requeues the job with a fresh
`prepare_dispatch` (R93), and the retries are bounded by `MAX_PREPUBLICATION_RETRIES` and
`preparation_deadline_at`, past which the store settles it `preparation_failed`, released
free (R29) - there is no port operation that fails a preparation, so the store's own
deadline and retry bound are what end it. Anything untyped propagates: W3's crash-only
service drains and exits for a restart, as the inference pool does.

The lease is renewed every third of `PREPARATION_LEASE_TTL_S` while the attempt runs (R52;
the store never renews it past the phase deadline). A drain cancels an attempt still
running at its bound: released, never prepared - the lease lapses and `recover` requeues.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import MediaRef
from .engine import prepared_request

log = logging.getLogger("infrx.worker")

TOKENIZE_PATH = "/tokenize"
# research/models/marlin2b/config.json `video_token_id`: one per merged video patch.
VIDEO_TOKEN_ID = 248057
# marlin-sop.md §1.5 (`tokens.py`, measured): 196 prompt tokens per two-frame patch, i.e.
# `size.longest_edge` (the clip's pixel budget) // 2048 at most.
TOKENS_PER_PATCH, PIXELS_PER_TOKEN = 196, 2048
# The attach lands just after the admission commits; the emulation this replaces waited 10 s.
ATTACH_WAIT_S, ATTACH_POLL_S = 10.0, 0.05


async def engine_prompt_tokens(engine, prepared, *,
                               timeout_s: float = DEFAULTS.preparation_timeout_s) -> int:
    """The engine's own count of `prepared`'s prompt, or `dependency_unavailable` - also when
    no answer comes within `timeout_s` (the preparation budget: a hung tokenizer must not hold
    a preparation runner past the phase its lease fences)."""
    body = engine.upstream_body(prepared)
    ask = {"model": body["model"], "messages": body["messages"], "add_generation_prompt": True}
    budget = body.get("mm_processor_kwargs")
    if budget:
        ask["mm_processor_kwargs"] = budget
    try:
        async with asyncio.timeout(timeout_s):
            answer = await engine.client.post(TOKENIZE_PATH, json=ask)
        answer.raise_for_status()
        found = answer.json()
    except (httpx.HTTPError, ValueError, TimeoutError) as failed:
        raise errors.DependencyUnavailable(
            f"the engine's tokenizer did not answer ({type(failed).__name__})") from None
    count = found.get("count") if isinstance(found, dict) else None
    tokens = found.get("tokens") if isinstance(found, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise errors.DependencyUnavailable("the engine's tokenizer answered no count")
    if tokens is not None and (not isinstance(tokens, list) or len(tokens) != count):
        raise errors.DependencyUnavailable("the engine's count disagrees with its tokens")
    if budget:
        most = budget["size"]["longest_edge"] // PIXELS_PER_TOKEN
        video = tokens.count(VIDEO_TOKEN_ID) if tokens is not None else -1
        if not most // TOKENS_PER_PATCH <= video <= most:
            raise errors.DependencyUnavailable(
                f"the engine counted {video} video tokens; the pinned profile makes "
                f"{most // TOKENS_PER_PATCH}..{most}")
    return count


@dataclass
class PreparationResult:
    """What one preparation attempt did (the loop's drain reads `cause or refusal`)."""

    job_id: str
    cause: str | None = None                 # "prepared" when it was
    refusal: str | None = None               # the error code when it was not
    prompt_tokens: int | None = None
    detail: str = ""


class PreparationRunner:
    """`WorkerLoop`'s runner for `prepare_dispatch` candidates (see the module docstring)."""

    def __init__(self, *, jobs, media, engine, worker_id: str,
                 limits: PilotSettings = DEFAULTS, attach_wait_s: float = ATTACH_WAIT_S,
                 renew_every_s: float | None = None) -> None:
        self.jobs = jobs                         # ports.JobStore (CreditWork in CREDIT)
        self.media = media                       # M's MediaPreparation
        self.engine = engine                     # VllmEngine: its body and its /tokenize
        self.worker_id = worker_id
        self.limits = limits
        self.attach_wait_s = attach_wait_s
        self.renew_every_s = renew_every_s or limits.preparation_lease_ttl_s / 3

    async def run(self, job_id: str) -> PreparationResult:
        try:
            lease = await self.jobs.claim_preparation(job_id, self.worker_id)
        except errors.DomainError as refused:
            return PreparationResult(job_id, refusal=refused.code, detail=str(refused))
        renewing = asyncio.create_task(self._renew(lease))
        try:
            count = await self._prepare(lease)
        except errors.DomainError as refused:
            log.warning("preparation of %s refused: %s", job_id, refused.code)
            return PreparationResult(job_id, refusal=refused.code, detail=str(refused))
        finally:
            renewing.cancel()
            await asyncio.gather(renewing, return_exceptions=True)
        return PreparationResult(job_id, cause="prepared", prompt_tokens=count)

    async def _prepare(self, lease) -> int:
        work = await self.jobs.load_work(lease)
        refs: tuple[MediaRef, ...] = ()
        if work.media_refs:
            refs = await self._media(lease.job_id)
        # The engine's view of the prepared request; `prepared_request` refuses a ref of
        # another organization (`not_found`), before anything is counted or stored.
        prepared = prepared_request(work.model_copy(update={"prepared_refs": refs}), 0,
                                    limits=self.limits)
        count = await engine_prompt_tokens(self.engine, prepared,
                                           timeout_s=self.limits.preparation_timeout_s)
        await self.jobs.prepared(lease, refs, prompt_tokens=count)
        return count

    async def _media(self, job_id: str) -> tuple[MediaRef, ...]:
        end = time.monotonic() + self.attach_wait_s
        while await self.media.attached(job_id) is None:
            if time.monotonic() >= end:
                raise errors.NotFound(f"job {job_id} has no durable attach")
            await asyncio.sleep(ATTACH_POLL_S)
        return await self.media.prepare(job_id, self.media.profile_version)

    async def _renew(self, lease) -> None:
        """R52: renew until cancelled. A refused renewal ends it; the fence then refuses
        `prepared` the same way, so nothing is written for a lost lease."""
        while True:
            await asyncio.sleep(self.renew_every_s)
            lease = await self.jobs.heartbeat(lease)
