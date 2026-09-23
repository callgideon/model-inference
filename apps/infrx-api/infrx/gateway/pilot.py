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

`adapters_from_env` is what `create_app` composes when a caller injects nothing: D5's
`PgCatalogDirectory`, D4's `PgStreamStore` and D2's `PgJobStore` (both regimes: the relay
dispatches on `ACCOUNTING_REGIME`, R86) on the one pool, and the object store `S3_MEDIA_BUCKET`
names. That last one has no adapter yet (M1 limit 2), so from settings a pilot refuses to
start rather than stage into process memory, which would make acceptance depend on
gateway-local bytes (02 step 1). Tests inject `objects=`.
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

from ..config import RuntimeMisconfigured, runtime_mode
from ..contracts import errors
from ..contracts.v2.records import CredentialAudience
from ..media import fetch
from ..media.prepare import ProcessingCache
from ..media.uploads import MediaUploads
from ..observe.metrics import Registry
from ..scheduling.reconcile import Reconciler
from ..state.catalog import PgCatalogDirectory
from ..state.jobstore import PgJobStore
from ..state.journal import PgStreamStore
from .routes import intake
from .routes.ingress import IngressDeps
from .routes.relay import CREDIT, Relay

log = logging.getLogger("infrx.gateway")

#: How often the lifetime task re-asks each readiness probe.
PROBE_EVERY_S = 5.0
#: One probe answer may take this long; past it the component reads unavailable.
PROBE_TIMEOUT_S = 10.0
#: How long shutdown waits for the relay's durable cancels still in flight (review S1). The
#: unit's graceful window (110 s) is spent before the lifespan's shutdown; docker stops at 120.
DRAIN_S = 5.0


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
            # `_answer` is bounded and never raises, so the thread always ends: a refusal
            # to start can finish (review C3) and no worker is left behind. The bound is
            # `wait_for`'s, so it holds for a check that awaits and lets its cancellation
            # through: one that blocks its thread, or suppresses or delays CancelledError
            # (3.12's wait_for waits for the cancelled check), holds this call with it
            # (review r2 COMP-N1; today's checks are cooperative).
            with ThreadPoolExecutor(1) as pool:
                self.value = pool.submit(asyncio.run, self._answer()).result()
        return self.value

    async def refresh(self) -> None:
        self.value = await self._answer()

    async def _answer(self) -> bool:
        """The check's answer within `timeout_s`; a check that hangs or fails reads False
        (review C2: a hung dependency never keeps a cached True)."""
        try:
            return bool(await asyncio.wait_for(self.check(), self.timeout_s))
        except Exception:
            log.warning("readiness probe failed or did not answer in %ss", self.timeout_s,
                        exc_info=True)
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
    belongs to the loop that opens it). Its connect is the stores' `Connect`."""
    from psycopg_pool import AsyncConnectionPool
    deployment = settings.deployment
    configure = configure_connection(deployment.database_pool_statement_timeout_ms)
    pool = AsyncConnectionPool(
        settings.pilot.database_url, open=False, kwargs={"autocommit": True},
        min_size=deployment.database_pool_min_size, max_size=deployment.database_pool_max_size,
        timeout=deployment.database_pool_connect_timeout_s, configure=configure)

    async def connect():
        if pool.closed:
            # Before `lifespan` opens the pool: `Probe`'s first answer, asked inside
            # `create_app` on a thread and loop of its own. One connection of its own,
            # configured as a pooled one is, closed by the caller.
            import psycopg
            conn = await psycopg.AsyncConnection.connect(settings.pilot.database_url,
                                                         autocommit=True)
            await configure(conn)
            return conn
        return _Pooled(pool, await pool.getconn())
    return pool, connect


def object_store(settings):
    """The object store that outlives the process, from `S3_MEDIA_BUCKET` - in every mode.
    ponytail: no S3 adapter exists (M1 limit 2, unowned), so a bucket name composes nothing
    yet; M's adapter replaces the second refusal with the store. Never process memory."""
    mode = runtime_mode(settings)
    if not settings.pilot.s3_media_bucket.strip():
        raise RuntimeMisconfigured(mode, ("S3_MEDIA_BUCKET",))
    raise RuntimeMisconfigured(mode, detail="S3_MEDIA_BUCKET is set, but no S3 object store "
                                            "adapter exists yet (M1 limit 2)")


def adapters_from_env(settings, **injected):
    """G2 R2-1: `build_ingress_deps`'s adapters from settings, for each one not injected -
    the object store first, so a refusal builds nothing; then the three stores on one pool
    (`lifespan` opens it)."""
    adapters = {name: value for name, value in injected.items() if value is not None}
    if "objects" not in adapters:
        adapters["objects"] = object_store(settings)
    if not {"catalog", "stream", "jobs"} <= adapters.keys():
        if not settings.pilot.database_url.strip():
            # Required in pilot by `validate_runtime`; in dev/test too once a store is built
            # from it - an empty DSN is libpq's defaults, some other database.
            raise RuntimeMisconfigured(runtime_mode(settings), ("DATABASE_URL",))
        pool, connect = connection_pool(settings)
        adapters = {"catalog": PgCatalogDirectory(connect),
                    "stream": PgStreamStore(connect, limits=settings.pilot),
                    "jobs": PgJobStore(connect, limits=settings.pilot), "pool": pool,
                    **adapters}
    return adapters


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
    relay: Any = None
    tasks: list = field(default_factory=list)


def build_ingress_deps(rt, *, catalog=None, stream=None, objects=None, jobs=None, index=None,
                       pool=None, consent_for=None) -> IngressDeps:
    """The `IngressDeps` G1R request 1 asks for, built from `rt.settings`, with the pieces
    other routers share put on `rt` (`media_store`, `large_bodies`, `metrics`, `lifetime`).
    The adapters come from `adapters_from_env` (or a test); `pool` is theirs, if any, for
    `lifespan` to open and close. `index` defaults to the Valkey index."""
    for name, value, owner in (("catalog", catalog, "a CatalogDirectory (D5)"),
                               ("stream", stream, "a StreamStore (D4)"),
                               ("objects", objects, "an object store that outlives the "
                                                    "process (M)"),
                               ("jobs", jobs, "a JobStore (D2)")):
        if value is None:
            raise RuntimeMisconfigured(rt.mode, detail=f"the pilot needs {owner}: {name}")
    settings = rt.settings
    pilot, deployment = settings.pilot, settings.deployment
    # I0 request 3 / M1 request 5: after the logging configuration (uvicorn's, which runs
    # before the app is built), so nothing configured later raises the transport loggers.
    fetch.silence_transport_logs()
    if getattr(rt, "metrics", None) is None:
        rt.metrics = Registry("gateway")
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
    rt.lifetime = Lifetime(probes=tuple(checks.values()), reconciler=reconciler, pool=pool,
                           relay=relay)
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
        if lifetime.relay is not None:
            await lifetime.relay.drain(DRAIN_S)     # before the pool it needs is closed
        if lifetime.pool is not None:
            await lifetime.pool.close()
