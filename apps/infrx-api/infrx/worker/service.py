"""W3: the worker process around `WorkerLoop` - the reaper, drain and readiness.

    service = WorkerService(loop=WorkerLoop(...), jobs=store, engine=engine,
                            concurrency=8, health_port=8002)
    report = await service.serve()          # SIGTERM/SIGINT -> drain -> the report

What it adds to the loop, each for a stated reason:

* **The reaper** (W2 limit 8; E3B dr03/dr04/dr06/dr13). `JobStore.recover()` once at
  start-up, before anything is claimed, then every `reap_interval_s`; every `IndexEvent`
  it returns is enqueued. `recover` takes no caller time (R7) and `enqueue` is
  replay-safe, so the cadence only bounds how late a lost lease is noticed (its TTL plus
  one interval). A failed `recover` or `enqueue` is counted and retried next tick: the
  outbox row is durable, and repairing a lost index entry is Q's reconciler's job.
* **Drain** (OPS-RECOVER). Stop claiming, wait up to `drain_s`, cancel what is still
  running - released, never settled - and record which job went which way. The default
  bound is the generation budget, so a drain cuts short nothing that would not have hit
  its own deadline; the service unit's stop timeout must exceed it (I2B).
* **Readiness and liveness, on loopback only** (never public: no route through the
  gateway, and a non-loopback bind is refused). `GET /readyz` is 200 only when the engine
  answers ready and the pool is running and not draining; its body says which of
  `engine` (`up`/`down`) and `loop` (`idle`/`busy`/`draining`/`stopped`) is the reason.
  `GET /livez` is 503 once the pool has stopped without being asked to. Counts only - no
  job id, no content.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import signal
from dataclasses import asdict, dataclass, field

from ..contracts.records import IndexEvent
from .loop import DrainReport, WorkerLoop

log = logging.getLogger("infrx.worker")

READY_PATH = "/readyz"
LIVE_PATH = "/livez"
# ponytail: a fixed cadence. A lost preparation lease (30 s TTL) is requeued within 40 s;
# a notification from the store replaces the timer if that ever matters.
REAP_INTERVAL_S = 10.0
HEALTH_TIMEOUT_S = 2.0          # an engine slower than this to say "ready" is not ready
REQUEST_TIMEOUT_S = 5.0         # a probe connection that sends nothing is dropped


@dataclass
class WorkerService:
    loop: WorkerLoop
    jobs: object                                 # ports.JobStore
    engine: object                               # ports.Engine
    concurrency: int = 1
    drain_s: float | None = None                 # None: the generation budget
    reap_interval_s: float = REAP_INTERVAL_S
    health_host: str = "127.0.0.1"
    health_port: int | None = None               # None: no listener (embedded use)
    reaped: int = 0
    reap_errors: int = 0
    last_drain: DrainReport | None = None
    _pool: asyncio.Task | None = field(default=None, repr=False)
    _reaper: asyncio.Task | None = field(default=None, repr=False)
    _server: asyncio.AbstractServer | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            loopback = ipaddress.ip_address(self.health_host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("worker readiness is never public: bind a loopback address")

    # --- the reaper -----------------------------------------------------------
    async def reap_once(self) -> int:
        """One `recover`, and every index event it produced enqueued. Returns how many."""
        try:
            produced = await self.jobs.recover()
        except Exception as failure:              # the store is down: try again next tick
            self.reap_errors += 1
            log.warning("recover failed: %s", type(failure).__name__)
            return 0
        events = [item for item in produced if isinstance(item, IndexEvent)]
        for event in events:
            try:
                await self.loop.scheduler.enqueue(event)
            except Exception as failure:          # the outbox row is durable; Q repairs
                self.reap_errors += 1
                log.warning("enqueue after recover failed: %s", type(failure).__name__)
        self.reaped += len(events)
        return len(events)

    async def _reap_forever(self) -> None:
        while True:
            await asyncio.sleep(self.reap_interval_s)
            await self.reap_once()

    # --- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        await self.reap_once()                    # a restart requeues what died with us
        self._pool = asyncio.create_task(
            self.loop.run(concurrency=self.concurrency, stop_when_idle=False), name="pool")
        self._reaper = asyncio.create_task(self._reap_forever(), name="reaper")
        if self.health_port is not None:
            self._server = await asyncio.start_server(self._probe, self.health_host,
                                                      self.health_port)

    async def stop(self) -> DrainReport:
        """Drain, then tear down. Returns (and logs) what the drain did to each job."""
        bound = self.loop.limits.generation_timeout_s if self.drain_s is None else self.drain_s
        report = await self.loop.drain(bound)
        if self._pool is not None:
            await asyncio.gather(self._pool, return_exceptions=True)
        if self._reaper is not None:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self.last_drain = report
        log.warning("drained: %d finished, %d released %s, ended %s", report.finished,
                    report.released, list(report.released_jobs), list(report.ended))
        return report

    async def serve(self, stop: asyncio.Event | None = None) -> DrainReport:
        """Run until SIGTERM/SIGINT (or `stop`), or until every runner has died; then
        drain. A process manager's stop timeout must exceed the drain bound."""
        stop = stop or asyncio.Event()
        running = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT)
        for sig in signals:
            running.add_signal_handler(sig, stop.set)
        try:
            await self.start()
            waiting = asyncio.create_task(stop.wait())
            await asyncio.wait({waiting, self._pool}, return_when=asyncio.FIRST_COMPLETED)
            waiting.cancel()
            return await self.stop()
        finally:
            for sig in signals:
                running.remove_signal_handler(sig)

    # --- readiness ------------------------------------------------------------
    def _loop_state(self) -> str:
        if self._pool is None or self._pool.done():
            return "stopped"
        if self.loop.draining:
            return "draining"
        return "busy" if self.loop.in_flight else "idle"

    async def readiness(self) -> dict:
        try:
            async with asyncio.timeout(HEALTH_TIMEOUT_S):
                engine_up = bool((await self.engine.health()).get("ready"))
        except Exception:                         # slow, refusing or broken: not up
            engine_up = False
        state = self._loop_state()
        return {"ready": engine_up and state in ("idle", "busy"),
                "live": state != "stopped" or self.loop.draining,
                "engine": "up" if engine_up else "down", "loop": state,
                "in_flight": len(self.loop.in_flight), "claimed": self.loop.claimed,
                "failures": len(self.loop.failures), "reaped": self.reaped,
                "reap_errors": self.reap_errors,
                "last_drain": None if self.last_drain is None else
                {key: value for key, value in asdict(self.last_drain).items()
                 if key in ("finished", "released", "claimed")}}

    async def _probe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A minimal HTTP/1.1 answer: `GET /readyz` or `GET /livez`, then close."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                request = (await reader.readline()).decode("latin-1").split()
                while (await reader.readline()).strip():
                    pass
            method, path = (request + ["", ""])[:2]
            if method != "GET" or path not in (READY_PATH, LIVE_PATH):
                status, body = 404, {"error": "not_found"}
            else:
                body = await self.readiness()
                status = 200 if body["ready" if path == READY_PATH else "live"] else 503
            raw = json.dumps(body).encode()
            reason = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[status]
            writer.write(f"HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\n"
                         f"content-length: {len(raw)}\r\nconnection: close\r\n\r\n".encode()
                         + raw)
            await writer.drain()
        except (TimeoutError, ConnectionError, ValueError):
            pass                                  # a broken probe gets no answer
        finally:
            writer.close()
