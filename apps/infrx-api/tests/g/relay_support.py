"""G2's in-process world: the contract fakes (JobStore + StreamStore), M's real media
adapter, W's real attempt runner over the scripted engine, and an ASGI driver that can
disconnect on cue. No sockets, no sleeps, one injected clock.

The app is the cutover's (`support.cutover_app`): the metered ingress with `Relay.accept`
as its `IngressDeps.accept`. The relay's `sleep` is `World.nap`, which yields to the loop and
runs whatever the case scheduled for "while the gateway waits" (`World.during`) - so a
case decides exactly what the store does between two polls.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, Callable

import httpx

from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.state import FakeJobStore, FakeStreamStore
from infrx.contracts.fakes.support import FailurePlan, FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (ChunkEventType, EngineEvent, ExecutionMode, JobState,
                                     SettlementState, TerminalCause, TerminalOutcome, Usage)
from infrx.media.fetch import MediaFetcher
from infrx.media.probe import Probed
from infrx.media.store import InMemoryObjectStore
from infrx.media.uploads import MediaUploads
from infrx.gateway.routes.relay import CREDIT, LEGACY, Relay
from infrx.worker import AttemptRunner
from infrx.worker.fakes import FakeUpstream

from . import support

IDS = support.IDS
TEXT = [{"role": "user", "content": "Describe the van."}]
CLIP_URL = "https://media.example.com/clip.mp4"
CLIP = b"\x00\x00\x00 ftypmp42" + b"\x00" * 64
VIDEO = [{"role": "user", "content": [{"type": "text", "text": "What happens?"},
                                      {"type": "video_url", "video_url": {"url": CLIP_URL}}]}]
PUBLIC = "93.184.216.34"
# The CREDIT regime's consumer: the fixture key row the seeded wallet directory knows.
CONSUMER_ROW = {"id": IDS.consumer_key, "org_id": IDS.consumer_org, "revoked_at": None,
                "audience": "consumer", "user_id": IDS.consumer_user,
                "created_by": IDS.consumer_user, "provider_org_id": None, "endpoint_id": None}


async def probe(data: bytes) -> Probed:
    """M2's probe, injected: what the bytes of the scripted clip measure."""
    return Probed(mime="video/mp4", duration_s=4.0, width=640, height=360, codec="h264")


def clip_transport() -> httpx.MockTransport:
    """The customer's media host, scripted: one clip, streamed (the fetcher reads raw)."""
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"},
                              stream=httpx.ByteStream(CLIP))
    return httpx.MockTransport(serve)


class Objects(InMemoryObjectStore):
    """M's object store, able to be down."""

    down = False

    async def put_if_absent(self, key, data, content_type):
        if self.down:
            raise OSError("object store unreachable at s3.internal:443")
        return await super().put_if_absent(key, data, content_type)


@dataclasses.dataclass
class World:
    regime: str = LEGACY
    limits: Any = DEFAULTS
    failures: FailurePlan = dataclasses.field(default_factory=FailurePlan)
    grant: str = "100"

    def __post_init__(self) -> None:
        if self.regime == CREDIT:
            harness = credit_jobstore_factory(self.limits)
            self.clock, self.jobs = harness.clock, harness.port
            self.stream = harness.extra["stream"]
            self.jobs.failures = self.stream.failures = self.failures
            self.catalog = support.catalog()
            self.jobs.catalog = self.catalog          # one catalog for ingress and store
            self.row, self.org = CONSUMER_ROW, IDS.consumer_org
            self.card = self.catalog.rate_cards[IDS.prod_deployment].rate_card_version
            # The fixture wallet starts with other holds on it; a case compares with these.
            self.seeded = {wallet_id: wallet.reserved_total
                           for wallet_id, wallet in self.jobs.credit_wallets.items()}
        else:
            self.clock, self.ids = FakeClock(), SequentialIds()
            self.jobs = FakeJobStore(self.clock, self.ids, limits=self.limits,
                                     failures=self.failures,
                                     prices={b.MODEL: b.DEFAULT_PRICE})
            self.stream = FakeStreamStore(self.jobs, failures=self.failures)
            self.catalog = support.catalog()
            self.row, self.org, self.card = support.ROW, support.ORG, ""
            self.jobs.grant(self.org, self.grant)
        self.results: dict[str, str] = {}
        self.objects = Objects()
        self.naps = 0
        self.during: list[Callable] = []          # run, in order, one per nap
        self.restart()

    def restart(self) -> None:
        """A gateway process: its relay, its media adapter (M's state is in process) and its
        app, over the stores and object store that outlive it. Called again, it is the next
        process after a restart."""
        self.relay = Relay(jobs=self.jobs, stream=self.stream, media=None, regime=self.regime,
                           catalog=self.catalog, active_rate_card_version=self.card,
                           results=self, limits=self.limits, clock=self.now_s, sleep=self.nap)
        fetcher = MediaFetcher(self.limits, resolve=self._resolve, transport=clip_transport())
        self.media = MediaUploads(self.objects, limits=self.limits, fetcher=fetcher,
                                  probe=probe, job_org=self.relay.job_org)
        self.relay.media = self.media
        self.app, _ = support.cutover_app(
            clock=self.now_s, sb=support.supabase(rows=(self.row,)),
            ingress_deps=support.deps(accept=self.relay.accept, catalog=self.catalog))

    @staticmethod
    async def _resolve(host):
        return [PUBLIC]

    # --- the relay's collaborators -------------------------------------------
    def now_s(self) -> float:
        return self.clock.now().timestamp()

    async def nap(self, seconds: float) -> None:
        self.naps += 1
        if self.during:
            step = self.during.pop(0)
            result = step()
            if asyncio.iscoroutine(result):
                await result
        await asyncio.sleep(0)

    async def put_result(self, job_id: str, text: str) -> str:
        self.results.setdefault(job_id, text)
        return f"infrx-result:{job_id}"

    async def read_result(self, org_id: str, ref: str) -> str:
        job_id = ref.removeprefix("infrx-result:")
        assert self.jobs.jobs[job_id].request.org_id == org_id, "a foreign result read"
        return self.results[job_id]

    # --- the store, from outside ---------------------------------------------
    def released(self, job) -> bool:
        """A CREDIT job's hold is off its wallet: the reserved total is back to the seed."""
        wallet_id = job.credit.wallet_id
        return self.jobs.credit_wallet(wallet_id).reserved_total == self.seeded[wallet_id]

    def only_job(self):
        (job,) = self.jobs.jobs.values()
        return job

    def journal(self, job_id: str) -> list:
        return list(self.stream.chunks.get(job_id, []))

    async def prepare(self, job_id: str | None = None) -> str:
        """M's preparation worker: the fenced preparation lease, `prepare`, `prepared`."""
        job_id = job_id or self.only_job().id
        lease = await self.jobs.claim_preparation(job_id, "prep-a")
        await self.jobs.prepared(lease, await self.media.prepare(job_id, "v1"))
        return job_id

    def upstream(self, fault: str = "none", **kw) -> FakeUpstream:
        return FakeUpstream(fault=fault, clock=self.clock, limits=self.limits, **kw)

    def runner(self, upstream: FakeUpstream, worker_id: str = "worker-a") -> AttemptRunner:
        return AttemptRunner(jobs=self.jobs, stream=self.stream,
                             engine=RecordKeysDropped(upstream.engine()),
                             clock=self.clock, worker_id=worker_id,
                             count_prompt_tokens=lambda work: upstream.prompt_tokens,
                             put_result=self.put_result, limits=self.limits)

    async def work(self, fault: str = "none", *, prepare: bool = True, **kw):
        """Prepare the one admitted job and run W's real attempt on it to the end."""
        job_id = await self.prepare() if prepare else self.only_job().id
        return await self.runner(self.upstream(fault, **kw)).run(job_id)

    async def lease(self, worker_id: str = "worker-a"):
        """A hand-driven inference lease on the one job, for cases that need to stop
        between two commits (the real runner writes a whole answer in one turn)."""
        job_id = await self.prepare()
        return await self.jobs.claim(job_id, worker_id)

    async def complete(self, lease, text: str = "Two people unload boxes."):
        """Settle a hand-driven lease as W2 does: the result object, then `complete`."""
        ref = await self.put_result(lease.job_id, text)
        return await self.jobs.complete(lease, TerminalOutcome(
            job_id=lease.job_id, state=JobState.succeeded, cause=TerminalCause.completed,
            usage=Usage.of(1200, 5), result_ref=ref,
            settlement_state=SettlementState.released_free, settled_at=self.clock.now()))

    async def commit(self, lease, *texts: str):
        return await self.stream.append(lease, tuple(
            EngineEvent(type=ChunkEventType.delta, payload={"visible": text, "raw": text})
            for text in texts))


# Keys the frozen `normalized_request.json` fixture carries in `parameters` that the record
# already consumed (`execution_mode`, `max_output_tokens`). W1's `VllmEngine.check_parameters`
# refuses them, so every validated request with `stream` or `max_tokens` settles
# `platform_error` today. The fix is W's (G2 integration request W-new: skip these three in
# `check_parameters`); this shim is that fix's effect, in the test world only.
RECORD_KEYS = frozenset({"stream", "max_tokens", "max_completion_tokens"})


class RecordKeysDropped:
    """W1's engine with the W-new fix applied from outside (test-only; see above)."""

    def __init__(self, engine) -> None:
        self.engine = engine

    def generate(self, lease, prepared):
        kept = {k: v for k, v in (prepared.parameters or {}).items() if k not in RECORD_KEYS}
        return self.engine.generate(lease, prepared.model_copy(update={"parameters": kept}))

    def __getattr__(self, name):
        return getattr(self.engine, name)


# --- the ASGI driver ------------------------------------------------------------
@dataclasses.dataclass
class Reply:
    messages: list

    @property
    def status(self) -> int | None:
        start = [m for m in self.messages if m["type"] == "http.response.start"]
        return start[0]["status"] if start else None

    @property
    def headers(self) -> httpx.Headers:
        start = [m for m in self.messages if m["type"] == "http.response.start"]
        return httpx.Headers(start[0]["headers"] if start else [])

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.messages
                        if m["type"] == "http.response.body")

    def json(self) -> dict:
        return json.loads(self.body)

    @property
    def frames(self) -> list[str]:
        return [frame for frame in self.body.decode().split("\n\n") if frame]

    def data(self) -> list:
        """The `data:` payload of every frame that has one, parsed where it is JSON."""
        out = []
        for frame in self.frames:
            for line in frame.split("\n"):
                if line.startswith("data: "):
                    raw = line[len("data: "):]
                    out.append(raw if raw == "[DONE]" else json.loads(raw))
        return out

    def events(self) -> list[str | None]:
        """Each frame's `event:` (None for a plain data frame, "comment" for a comment)."""
        out = []
        for frame in self.frames:
            if frame.startswith(":"):
                out.append("comment")
                continue
            named = [line[len("event: "):] for line in frame.split("\n")
                     if line.startswith("event: ")]
            out.append(named[0] if named else None)
        return out

    def text(self) -> str:
        return "".join(choice.get("delta", {}).get("content", "")
                       for item in self.data() if isinstance(item, dict)
                       for choice in item.get("choices", ()))


def body(messages=TEXT, *, stream: bool = False, model: str = support.MODEL_REVISION, **extra):
    return {"model": model, "messages": messages, "stream": stream, **extra}


async def call(app, payload: dict, *, key: str | None = None, headers: dict | None = None,
               leave: asyncio.Event | None = None, on_send: Callable | None = None) -> Reply:
    """One `POST /v1/chat/completions` over ASGI. Once `leave` is set, the next `receive`
    answers `http.disconnect`; `on_send(message)` sees every message as it is sent (a case
    can set `leave` from it, or raise to make a send fail)."""
    raw = json.dumps(payload).encode()
    sent: list = []
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        await (leave or asyncio.Event()).wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if on_send is not None:
            on_send(message)

    head = {**support.RAW, **({"idempotency-key": key} if key else {}), **(headers or {})}
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "POST", "path": support.CHAT_PATH,
             "raw_path": support.CHAT_PATH.encode(), "query_string": b"", "root_path": "",
             "scheme": "http", "client": ("198.51.100.7", 40000), "server": ("gw", 8001),
             "headers": [(k.lower().encode(), v.encode()) for k, v in head.items()]}
    await app(scope, receive, send)
    return Reply(sent)


def run(coroutine):
    return asyncio.run(coroutine)


MODES = {"sync": ExecutionMode.sync, "stream": ExecutionMode.stream}
