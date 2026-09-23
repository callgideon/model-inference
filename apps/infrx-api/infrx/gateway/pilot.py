"""G2 item 5: the pilot composition - `IngressDeps` built from the real adapters.

    rt.ingress = pilot.build_ingress_deps(rt, catalog=..., stream=..., objects=...)
    app = FastAPI(lifespan=pilot.lifespan, ...)

What `create_app` calls at the cutover, once per process:

* `PgJobStore` over a psycopg pool whose connections `set role service_role` (D2 request 7)
  and carry the deployment's statement timeout;
* the `Relay` (G2's acceptor, sync wait and SSE relay), dispatching admission on
  `ACCOUNTING_REGIME` and holding the CREDIT pins to `ACTIVE_RATE_CARD_VERSION`;
* one `MediaUploads` (M3 request 3), exposed as `rt.media_store` - the instance G4U's upload
  router finalizes into - and one `LargeBodies`, `rt.large_bodies`, shared with it;
* the Q3 `Reconciler` over the Valkey index, run for the process lifetime by `lifespan`;
* the gateway `Registry` (I3B request 1) and the two readiness probes `REQUIRED_CHECKS`
  names, as sync callables over cached answers the lifetime task refreshes.

Three collaborators have no real adapter yet, so they are parameters and a pilot is not
built without them: the psycopg `CatalogDirectory` over 0007 (D5), D4's `PgStreamStore`,
and an object store that outlives the process (M: no S3 adapter; staging into process
memory would make acceptance depend on gateway-local bytes, 02 step 1).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import RuntimeMisconfigured
from ..contracts import errors
from ..contracts.v2.records import CredentialAudience
from ..media import fetch
from ..media.prepare import ProcessingCache
from ..media.uploads import MediaUploads
from ..observe.metrics import Registry
from ..scheduling.reconcile import Reconciler
from ..state.jobstore import PgJobStore
from .routes import intake
from .routes.ingress import IngressDeps
from .routes.relay import CREDIT, Relay

log = logging.getLogger("infrx.gateway")

#: How often the lifetime task re-asks each readiness probe.
PROBE_EVERY_S = 5.0
#: The first answer of a probe, asked synchronously at registration, may take this long.
PROBE_TIMEOUT_S = 10.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Probe:
    """One readiness answer that `ingress.component_state` can read: sync, and after its
    first answer never blocking - `/readyz` is async and must not nest an event loop.

    The lifetime task refreshes it (`refresh`). The first call comes from `assert_startup`
    inside `create_app`, where a sync caller may be inside a running loop (`uvicorn
    --factory`), so that one answer is asked on a thread of its own: `check` must therefore
    not depend on a loop-bound resource (the stores' probe paths use per-call connections).
    """

    def __init__(self, check, timeout_s: float = PROBE_TIMEOUT_S) -> None:
        self.check = check
        self.timeout_s = timeout_s
        self.value: bool | None = None

    def __call__(self) -> bool:
        if self.value is None:
            pool = ThreadPoolExecutor(1)
            try:
                self.value = pool.submit(asyncio.run, self._answer()).result(self.timeout_s)
            except Exception:
                log.exception("readiness probe did not answer in %ss", self.timeout_s)
                self.value = False
            finally:
                pool.shutdown(wait=False)
        return self.value

    async def refresh(self) -> None:
        self.value = await self._answer()

    async def _answer(self) -> bool:
        try:
            return bool(await self.check())
        except Exception:
            log.warning("readiness probe failed", exc_info=True)
            return False


def journal_check(stream):
    """D4 integration request 2: the journal is ready when `usage()` answers."""
    async def check() -> bool:
        await stream.usage()
        return True
    return check


def price_check(catalog, model_id: str, regime: str, active_card: str):
    """The served model resolves for a consumer and has an approved card - in the CREDIT
    regime, the very card this deployment was approved to serve."""
    async def check() -> bool:
        deployment = await catalog.resolve(model_id, audience=CredentialAudience.consumer,
                                           endpoint_id=None)
        card = deployment and await catalog.active_rate_card(deployment.deployment_revision_id)
        return card is not None and (regime != CREDIT
                                     or card.rate_card_version == active_card)
    return check


def configure_connection(statement_timeout_ms: int):
    """The pool's `configure` hook (D2 request 7): 0004's BYPASSRLS is not inherited, so the
    role is SET on every connection, as PostgREST does; and no statement runs unbounded."""
    async def configure(conn) -> None:
        await conn.execute("set role service_role")
        await conn.execute(f"set statement_timeout = {int(statement_timeout_ms)}")
    return configure


class _Pooled:
    """A pooled connection `PgJobStore` may `close()`: closing hands it back to the pool."""

    def __init__(self, pool, conn) -> None:
        self._pool, self._conn = pool, conn

    def execute(self, *args, **kw):
        return self._conn.execute(*args, **kw)

    async def close(self) -> None:
        await self._pool.putconn(self._conn)


def connection_pool(settings):
    """A psycopg pool from `DATABASE_URL` and `DATABASE_POOL_*`, opened by `lifespan` (a pool
    belongs to the loop that opens it). Its connect is `PgJobStore`'s `Connect`."""
    from psycopg_pool import AsyncConnectionPool
    deployment = settings.deployment
    pool = AsyncConnectionPool(
        settings.pilot.database_url, open=False, kwargs={"autocommit": True},
        min_size=deployment.database_pool_min_size, max_size=deployment.database_pool_max_size,
        timeout=deployment.database_pool_connect_timeout_s,
        configure=configure_connection(deployment.database_pool_statement_timeout_ms))

    async def connect():
        return _Pooled(pool, await pool.getconn())
    return pool, connect


def valkey_index(pilot):
    from ..scheduling import valkey
    try:
        client = valkey.connect(pilot)
    except errors.InvalidRequest:
        raise RuntimeMisconfigured("pilot", ("VALKEY_URL",)) from None
    return valkey.ValkeyScheduler(client, _utc_now, limits=pilot)


@dataclass
class Lifetime:
    """What `lifespan` runs for the process: the probes' refresh and the Q3 relay."""

    probes: tuple[Probe, ...]
    reconciler: Any
    pool: Any = None
    tasks: list = field(default_factory=list)


def build_ingress_deps(rt, *, catalog=None, stream=None, objects=None, jobs=None, index=None,
                       consent_for=None) -> IngressDeps:
    """The `IngressDeps` G1R request 1 asks for, built from `rt.settings`, with the pieces
    other routers share put on `rt` (`media_store`, `large_bodies`, `metrics`, `lifetime`).
    `jobs`/`index` default to the real PostgreSQL store and Valkey index."""
    for name, value, owner in (("catalog", catalog, "a CatalogDirectory (D5)"),
                               ("stream", stream, "a StreamStore (D4)"),
                               ("objects", objects, "an object store that outlives the "
                                                    "process (M)")):
        if value is None:
            raise RuntimeMisconfigured(rt.mode, detail=f"the pilot needs {owner}: {name}")
    settings = rt.settings
    pilot, deployment = settings.pilot, settings.deployment
    # I0 request 3 / M1 request 5: after the logging configuration (uvicorn's, which runs
    # before the app is built), so nothing configured later raises the transport loggers.
    fetch.silence_transport_logs()
    if getattr(rt, "metrics", None) is None:
        rt.metrics = Registry("gateway")
    pool = None
    if jobs is None:
        pool, connect = connection_pool(settings)
        jobs = PgJobStore(connect, limits=pilot)
    reconciler = Reconciler(store=jobs, index=index if index is not None else valkey_index(pilot),
                            now=_utc_now, worker_id=f"gateway-{uuid.uuid4().hex[:8]}")
    relay = Relay(jobs=jobs, stream=stream, media=None, regime=deployment.accounting_regime,
                  catalog=catalog, active_rate_card_version=pilot.active_rate_card_version,
                  limits=pilot, clock=rt.clock, registry=rt.metrics)
    relay.media = rt.media_store = MediaUploads(
        objects, cache=ProcessingCache(pilot.processing_cache_dir,
                                       ttl_s=pilot.processing_cache_ttl_s),
        limits=pilot, fetcher=fetch.MediaFetcher(pilot, allowed_mime=settings.allowed_video_mime),
        job_org=relay.job_org)
    rt.large_bodies = intake.LargeBodies(limit=deployment.large_body_limit,
                                         threshold=deployment.large_body_threshold_bytes)
    checks = {"price_source": Probe(price_check(catalog, settings.model_id,
                                                deployment.accounting_regime,
                                                pilot.active_rate_card_version)),
              "journal": Probe(journal_check(stream))}
    rt.relay = relay
    rt.lifetime = Lifetime(probes=tuple(checks.values()), reconciler=reconciler, pool=pool)
    return IngressDeps(accept=relay.accept, checks=checks, consent_for=consent_for,
                       catalog=catalog, large_bodies=rt.large_bodies)


async def _refresh(probes, stop: asyncio.Event) -> None:
    while not stop.is_set():
        for probe in probes:
            await probe.refresh()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), PROBE_EVERY_S)


@contextlib.asynccontextmanager
async def lifespan(app):
    """The process lifetime (Q3 request 2): open the pool, run the relay and the probes'
    refresh until shutdown, then stop them and close the pool. An app built without a
    pilot composition (`rt.lifetime` absent) has nothing to run."""
    lifetime = getattr(app.state.runtime, "lifetime", None)
    if lifetime is None:
        yield
        return
    stop = asyncio.Event()
    if lifetime.pool is not None:
        await lifetime.pool.open()
    lifetime.tasks = [asyncio.create_task(lifetime.reconciler.run(stop)),
                      asyncio.create_task(_refresh(lifetime.probes, stop))]
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*lifetime.tasks, return_exceptions=True)
        if lifetime.pool is not None:
            await lifetime.pool.close()
