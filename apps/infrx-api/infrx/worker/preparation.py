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
3. **W5 (RV-05): the durable execution-ready manifest, for EVERY job** - text-only as well
   as video. Nothing is prepared, counted or stored for a job until its manifest is
   committed where this process reads it: with F2C's `ReadinessStore` wired (`readiness=`,
   D10's adapter) the claim goes through its marker-gated `claim_preparation` and the
   manifest is `readiness(job_id)` - `None` (no marker: the previous runtime's job, or an
   admission that never completed) is NOT READY and is never read as legacy-ready from any
   other record; without it (a pre-D10 composition) the manifest is the durable attach the
   admitting gateway writes after its late card/capability rechecks (R99 (c)), where a
   PostgreSQL text job has none, so it fails closed. The wait is bounded (`ATTACH_WAIT_S`;
   `not_claimable`, F2C's `not_ready`, after it). An EMPTY manifest is a completed one (a
   text job), never missing work. Then, only when the request carries media, M's
   `prepare(job_id, profile)` over the object store and the shared processing cache.
4. **The count, exactly as the engine counts it**: vLLM's `POST /tokenize` on the chat body
   `VllmEngine.upstream_body` would send (the chat template; for a video, the local file and
   the pinned `mm_processor_kwargs`). The answer is checked, never replaced or estimated: an
   integer `count` and exactly that many `tokens` (vLLM always answers both), and for a
   video between one and 196
   `video_token_id`s per two-frame patch of the pinned budget (`models/marlin2b/tokens.py`,
   marlin-sop.md §1.5) - one placeholder is a tokenizer that skipped the multimodal
   processor, and more than the budget is an engine not running the pinned profile. No
   answer within `PREPARATION_TIMEOUT_S`, or one that fails a check, is
   `dependency_unavailable`.
5. `prepared(lease, refs, prompt_tokens=count)`, logged at INFO with the count and the
   tokenizer's latency (`prepared <job>: <n> prompt tokens (engine /tokenize, <ms> ms)`); a
   lost claim is logged at INFO too, a refused attempt at WARNING.

**TOKCOST: a repeated video body is counted by the engine once per process.** The real
engine's `/tokenize` decodes and preprocesses the clip (vLLM's `create_tokenize` renders
with `skip_mm_cache=True`: its own processor-only cache, never the one the chat route
reads), measured at 16.8 s for a 5 s clip on the pilot. `CountMemo` keeps the engine's
CHECKED count of a video body, keyed by `memo_key`: the job's serving revision (its
`model_revision` and, in the CREDIT regime, its pinned `serving_version_id`), every prepared
media digest at its profile and the exact `/tokenize` body (the served model, the rebuilt
messages with the tenant's own `file://` path, the generation prompt, the pinned budget) -
so the memo answers only a question the engine already answered, word for word, for the
same bytes and the same revision (R105), and never across tenants (the path names the
organization). A hit is logged `(memo of engine /tokenize, <ms> ms)`, never as the engine's
latency. Text is always asked (4-6 ms, and no digest of a text prompt is held). The memo
lives in this process only: the worker is `PartOf=` the engine's unit, so an engine restart
- the only way a serving revision changes - restarts the worker and empties it. Bounded by
`MEMO_ENTRIES` and by `PROCESSING_CACHE_TTL_S` (it never outlives the retention of the media
it counted).

**A typed refusal anywhere prepares nothing** (a fetch or stage `not_found`, an
`unsupported_media`, the tokenizer's `dependency_unavailable`, a count past the job's
ceiling): the lease is left to lapse, `recover` requeues the job with a fresh
`prepare_dispatch` (R93), and the retries are bounded by `MAX_PREPUBLICATION_RETRIES` and
`preparation_deadline_at`, past which the store settles it `preparation_failed`, released
free (R29) - there is no port operation that fails a preparation, so the store's own
deadline and retry bound are what end it. Anything untyped propagates - from the attempt
or from its lease renewal (the store unreachable on `heartbeat`) - and W3's crash-only
service drains and exits for a restart, as the inference pool does.

The lease is renewed every third of `PREPARATION_LEASE_TTL_S` while the attempt runs (R52;
the store never renews it past the phase deadline). A typed refusal of a renewal
(`stale_lease`, `already_terminal`) only stops renewing: the fence refuses `prepared` the
same way. A drain cancels an attempt still running at its bound: released, never prepared -
the lease lapses and `recover` requeues.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
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
# TOKCOST: the video counts one worker process remembers (a key and an int, ~200 bytes each).
MEMO_ENTRIES = 1024


def tokenize_body(engine, prepared) -> dict:
    """The exact `/tokenize` body for `prepared`: what the chat route will be sent, as
    `VllmEngine.upstream_body` builds it (the served model, the rebuilt messages, the pinned
    `mm_processor_kwargs` of a video), plus the generation prompt."""
    body = engine.upstream_body(prepared)
    ask = {"model": body["model"], "messages": body["messages"], "add_generation_prompt": True}
    budget = body.get("mm_processor_kwargs")
    if budget:
        ask["mm_processor_kwargs"] = budget
    return ask


def memo_key(work, prepared, ask: dict) -> str:
    """What a memoized count answers for, and nothing else: the job's serving revision (its
    `model_revision` and, in the CREDIT regime, its pinned `serving_version_id` - an alias
    alone can move to another revision), every prepared media digest at its profile, and the
    exact `/tokenize` body `ask` (whose `file://` path also names the organization)."""
    facts = [prepared.model_revision, getattr(work, "serving_version_id", None),
             sorted(f"{ref.digest}@{ref.profile_version}" for ref in prepared.media), ask]
    return hashlib.sha256(json.dumps(facts, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class CountMemo:
    """TOKCOST: the engine's checked count of an exact video body, in this process (see the
    module docstring). At most `entries` (the least recently used goes first) and none older
    than `ttl_s` since the engine answered."""

    def __init__(self, *, ttl_s: float, entries: int = MEMO_ENTRIES,
                 clock=time.monotonic) -> None:
        self.ttl_s, self.entries, self.clock = ttl_s, entries, clock
        self.counts: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def get(self, key: str) -> int | None:
        found = self.counts.get(key)
        if found is None:
            return None
        if self.clock() - found[0] >= self.ttl_s:
            del self.counts[key]
            return None
        self.counts.move_to_end(key)
        return found[1]

    def put(self, key: str, count: int) -> None:
        self.counts[key] = (self.clock(), count)
        self.counts.move_to_end(key)
        if len(self.counts) > self.entries:
            self.counts.popitem(last=False)


async def engine_prompt_tokens(engine, prepared, *,
                               timeout_s: float = DEFAULTS.preparation_timeout_s) -> int:
    """The engine's own count of `prepared`'s prompt, or `dependency_unavailable` - also when
    no answer comes within `timeout_s` (the preparation budget: a hung tokenizer must not hold
    a preparation runner past the phase its lease fences)."""
    ask = tokenize_body(engine, prepared)
    budget = ask.get("mm_processor_kwargs")
    try:
        async with asyncio.timeout(timeout_s):
            answer = await engine.client.post(TOKENIZE_PATH, json=ask)
        answer.raise_for_status()
        found = answer.json()
    except (httpx.HTTPError, ValueError, TimeoutError) as failed:
        raise errors.DependencyUnavailable(
            f"the engine's tokenizer did not answer ({type(failed).__name__})") from None
    return checked_count(found, budget)


def checked_count(found, budget: dict | None) -> int:
    """R105: the `/tokenize` answer `found` is the count only when it is one (an integer and
    exactly that many tokens; for a video, `budget`, the pinned number of `video_token_id`s),
    or `dependency_unavailable`. Nothing is memoized before this has passed (TOKCOST)."""
    count = found.get("count") if isinstance(found, dict) else None
    tokens = found.get("tokens") if isinstance(found, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise errors.DependencyUnavailable("the engine's tokenizer answered no count")
    if not isinstance(tokens, list) or len(tokens) != count:
        raise errors.DependencyUnavailable("the engine's count disagrees with its tokens")
    if budget:
        most = budget["size"]["longest_edge"] // PIXELS_PER_TOKEN
        video = tokens.count(VIDEO_TOKEN_ID)
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
                 renew_every_s: float | None = None, memo: CountMemo | None = None,
                 readiness=None) -> None:
        self.jobs = jobs                         # ports.JobStore (CreditWork in CREDIT)
        # W5: F2C's `ReadinessStore` (D10's adapter) - the marker-gated claim and the manifest.
        # None only in a pre-D10 composition (the durable attach record is the manifest).
        self.readiness = readiness
        self.media = media                       # M's MediaPreparation
        self.engine = engine                     # VllmEngine: its body and its /tokenize
        self.worker_id = worker_id
        self.limits = limits
        self.attach_wait_s = attach_wait_s
        self.renew_every_s = renew_every_s or limits.preparation_lease_ttl_s / 3
        # One per process: the pool's runners share this runner (TOKCOST).
        self.memo = CountMemo(ttl_s=limits.processing_cache_ttl_s) if memo is None else memo

    async def run(self, job_id: str) -> PreparationResult:
        try:
            lease = await (self.readiness or self.jobs).claim_preparation(job_id, self.worker_id)
        except errors.DomainError as refused:
            # The index is a hint (02 §4): a candidate offered twice, or a job that moved
            # on, is a lost claim - answered, never a dead runner.
            log.info("preparation of %s not claimed: %s", job_id, refused.code)
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
            died = None if renewing.cancelled() else renewing.exception()
            if died is not None and not isinstance(died, errors.DomainError):
                raise died                        # the store under the renewal: crash-only
        return PreparationResult(job_id, cause="prepared", prompt_tokens=count)

    async def _prepare(self, lease) -> int:
        work = await self.jobs.load_work(lease)
        # W5 (RV-05): the barrier, for text as for media - before anything is counted.
        await self._ready(lease.job_id)
        refs: tuple[MediaRef, ...] = ()
        if work.media_refs:
            refs = await self._media(lease.job_id)
        # The engine's view of the prepared request; `prepared_request` refuses a ref of
        # another organization (`not_found`), before anything is counted or stored.
        prepared = prepared_request(work.model_copy(update={"prepared_refs": refs}), 0,
                                    limits=self.limits)
        # TOKCOST: a video body the engine already counted here is its memoized answer.
        key = memo_key(work, prepared, tokenize_body(self.engine, prepared)) if refs else None
        asked = time.monotonic()
        count, source = (self.memo.get(key) if key else None), "memo of engine /tokenize"
        if count is None:
            count, source = await self._ask(prepared), "engine /tokenize"
            if key:
                self.memo.put(key, count)
        took_ms = (time.monotonic() - asked) * 1000
        await self.jobs.prepared(lease, refs, prompt_tokens=count)
        log.info("prepared %s: %d prompt tokens (%s, %.0f ms)", lease.job_id, count, source,
                 took_ms)
        return count

    async def _ask(self, prepared) -> int:
        """The engine's own count, checked, within the preparation budget."""
        count = await engine_prompt_tokens(self.engine, prepared,
                                           timeout_s=self.limits.preparation_timeout_s)
        return count

    async def _ready(self, job_id: str) -> tuple[MediaRef, ...]:
        """The job's committed source manifest - `()` for a text job - waited for within
        `attach_wait_s`, or `not_claimable` (F2C's `not_ready`): nothing is prepared."""
        end = time.monotonic() + self.attach_wait_s
        while (manifest := await self._manifest(job_id)) is None:
            if time.monotonic() >= end:
                raise errors.NotClaimable(f"job {job_id} is not execution-ready")
            await asyncio.sleep(ATTACH_POLL_S)
        return manifest

    async def _manifest(self, job_id: str) -> tuple[MediaRef, ...] | None:
        """The committed marker's sources (`None`: no marker - not ready, whatever else is
        recorded), or without a `ReadinessStore` the durable attach record (R99 (c): a
        pre-D10 PostgreSQL store records no attach of NO media, so a text job there is
        never ready - fail closed until D10's marker is wired)."""
        if self.readiness is None:
            return await self.media.attached(job_id)
        ready = await self.readiness.readiness(job_id)
        return None if ready is None else tuple(source.ref for source in ready.sources)

    async def _media(self, job_id: str) -> tuple[MediaRef, ...]:
        refs = await self.media.prepare(job_id, self.media.profile_version)
        # Verifier F3: MediaPreparation keeps a per-job entry for the gateway's relay; nothing in
        # this long-lived process reads it, so it must not grow by one video job forever.
        self.media.prepared_by_job.pop(job_id, None)
        return refs

    async def _renew(self, lease) -> None:
        """R52: renew until cancelled. A typed refusal ends it (the fence then refuses
        `prepared` the same way, so nothing is written for a lost lease); an untyped failure
        ends it too and `run` re-raises it."""
        while True:
            await asyncio.sleep(self.renew_every_s)
            lease = await self.jobs.heartbeat(lease)
