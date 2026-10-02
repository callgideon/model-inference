"""The gateway's composition root (G2 item 5 onward): `IngressDeps` and the runtime's stores,
built from settings once per process by `create_app` (`gateway/app.py`):

    rt.ingress = pilot.build_ingress_deps(rt, **pilot.adapters_from_env(settings, ...))
    app = FastAPI(lifespan=pilot.lifespan, ...)

Every composition here, its switch and its callers (A15):

* `adapters_from_env` - `create_app`, `capture` (WR-C6-CAPTURE). The object store first
  (`object_store`, `S3_MEDIA_BUCKET` answering HeadBucket, else startup is refused - never
  process memory, M1-L2), then on one pool (`connection_pool`: `set role service_role` and
  the statement timeout per connection, D2 request 7; never on 0021's dedicated logins, R127;
  nothing session-level on the transaction pooler, WR-I8-1): D5's `PgCatalogDirectory`, D4's
  `PgStreamStore`, D2's `PgJobStore` (both regimes, R86), M's `PgAttachments`, D10's
  `PgLifecycle` (`_pg_lifecycle`, also the admission's `ReadinessStore`), and the switched ones:
  `ROLLOUT_ROUTING` -> `_rollouts` (R1's router over D9 with `NoShadows`, `infrx_runtime`
  login only), `TRACE_PUMPS` -> `capture.adapters`, `FEEDBACK_API` -> `_pg_feedback`, and the
  Lab's `LAB_*` switches -> `infrx.lab.compose` (`lab_surfaces`, `lab_checkpoints`).
* `build_ingress_deps` - `create_app`: the `Relay` (admission on `ACCOUNTING_REGIME`, CREDIT
  pinned to `ACTIVE_RATE_CARD_VERSION`; `admission_readiness`), `MediaUploads` as
  `rt.media_store` with `LargeBodies`, the Q3 `Reconciler` over `valkey_index`, the two
  readiness `Probe`s (`price_check`, `journal_check`), `TRACE_EXPORT_API` -> `_trace_export`,
  and every Lab surface on `rt` only when its switch is on.
* `build_info` - `create_app` and the worker: `infrx_build_info{revision, image}`.
* `lifespan` - the app's: opens the pool, runs the reconciler, the probes and the capture
  pump, drains the relay, closes the pool.
* `connection_pool`, `object_store`, `valkey_index`, `build_info` - also `infrx.worker`.
* The login rules (`login_user`, `dedicated_login`, `DEDICATED_LOGINS`) are
  `state.jobstore`'s (A9), re-exported here.

The Lab compositions moved to `infrx.lab.compose` (A1); the names tests import
(`_lab`, `_lab_2`, `_lab_checkpoints`, `_lab_traces`, `_teachers`, `lab_releases`,
`lab_operations`, `control_serving`, `lab_optimizations`, `ReleaseRecords`, ...) are
re-exported here, the same objects.
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
from ..console.actions import ConsoleActions
from ..console.data_use import DataUse
from ..contracts import errors
from ..contracts.limits import env_name
from ..contracts.v2.lifecycle import AdmissionExpectation, ReadinessStore
from ..lab import compose
from ..lab.compose import (  # noqa: F401 - A1: re-exported by their old names (tests)
    SHOWN, ReleaseProposals, ReleaseRecords, ReportUnavailable, RunLedger, _progress, _z,
    control_serving, lab_operations, lab_optimizations, lab_releases)
from ..lab.compose import lab_checkpoints as _lab_checkpoints  # noqa: F401
from ..lab.compose import lab_evaluations_pipelines_releases as _lab_2  # noqa: F401
from ..lab.compose import lab_surfaces as _lab  # noqa: F401
from ..lab.compose import lab_teachers as _teachers  # noqa: F401
from ..lab.compose import lab_traces as _lab_traces  # noqa: F401
from ..media import fetch
from ..media.attachments import PgAttachments
from ..media.prepare import ProcessingCache
from ..media.uploads import MediaUploads
from ..observe.metrics import Registry
from ..scheduling.reconcile import Reconciler
from ..state.catalog import PgCatalogDirectory
from ..state.jobstore import (  # noqa: F401 - A9: re-exported, `jobstore` owns the parser
    DEDICATED_LOGINS, PgJobStore, dedicated_login, login_user, session_state_allowed)
from ..state.journal import PgStreamStore
from . import capture as trace_capture
from .routes import intake, models
from .routes.ingress import IngressDeps
from .routes.relay import Relay

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


def configure_connection(statement_timeout_ms: int, *, session_state: bool = True,
                         set_role: bool = True):
    """The pool's `configure` hook (D2 request 7): 0004's BYPASSRLS is not inherited, so the
    role is SET on every connection, as PostgREST does; and no statement runs unbounded.
    Without `session_state` (the transaction pooler, WR-I8-1) it sends nothing: a session
    SET there is lost and leaked, so the login role's own defaults carry both. Without
    `set_role` (a dedicated login, R127 / E3C F-1) only the statement timeout is SET."""
    async def configure(conn) -> None:
        if not session_state:
            return
        if set_role:
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
    configure = configure_connection(deployment.database_pool_statement_timeout_ms,
                                     session_state=session_state_allowed(
                                         settings.pilot.database_url),
                                     set_role=not dedicated_login(settings.pilot.database_url))
    # prepare_threshold=None: no server-side prepared statements (unsupported on the
    # transaction pooler, WR-I8-1; harmless on the session port)
    pool = AsyncConnectionPool(
        settings.pilot.database_url, open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
        min_size=deployment.database_pool_min_size, max_size=deployment.database_pool_max_size,
        timeout=deployment.database_pool_connect_timeout_s, configure=configure)

    async def connect():
        if pool.closed:
            # Before `lifespan` opens the pool: `Probe`'s first answer, asked inside
            # `create_app` on a thread and loop of its own. One connection of its own,
            # configured as a pooled one is, closed by the caller.
            import psycopg
            conn = await psycopg.AsyncConnection.connect(settings.pilot.database_url,
                                                         autocommit=True,
                                                         prepare_threshold=None)
            await configure(conn)
            return conn
        return _Pooled(pool, await pool.getconn())
    return pool, connect


def object_store(settings):
    """The object store that outlives the process, from `S3_MEDIA_BUCKET` - in every mode,
    never process memory (M1-L2). The bucket must answer HeadBucket with the environment's
    credentials before anything is served; a refusal names the setting and the S3 error
    code, never the bucket or the endpoint."""
    mode = runtime_mode(settings)
    if not settings.pilot.s3_media_bucket.strip():
        raise RuntimeMisconfigured(mode, ("S3_MEDIA_BUCKET",))
    from ..media.s3 import S3ObjectStore, reason      # stdlib only; botocore on connect
    deployment = settings.deployment
    try:
        objects = S3ObjectStore.connect(settings.pilot.s3_media_bucket,
                                        deployment.s3_media_prefix, deployment.s3_endpoint_url)
        objects.probe()
    except Exception as failure:          # noqa: BLE001 - every failure refuses startup
        raise RuntimeMisconfigured(mode, detail="S3_MEDIA_BUCKET did not answer HeadBucket "
                                                f"({reason(failure)})") from None
    return objects


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
        lifecycle = _pg_lifecycle(connect, settings)
        adapters = {"catalog": PgCatalogDirectory(connect),
                    "stream": PgStreamStore(connect, limits=settings.pilot),
                    # MPILOT gap 2: M's attach, durable where the worker reads it
                    "attachments": PgAttachments(connect),
                    # M5 (RV-02): upload tickets and content rows, on the same pool
                    "lifecycle": lifecycle,
                    # W5 wiring 4: the same adapter is the admission's ReadinessStore -
                    # only beside the job store it was built with (one database)
                    **({} if "jobs" in adapters else {"readiness": lifecycle}),
                    "jobs": PgJobStore(connect, limits=settings.pilot), "pool": pool,
                    # LAB-API (WR-LAB-API-1): only the Lab surfaces the deployment enables
                    # (kept above G4F: its last two lines anchor given_stores_replaced)
                    **compose.lab_surfaces(settings, connect, adapters["objects"]),
                    **compose.lab_checkpoints(settings, connect),
                    # R1 (WR-R1-3-C): only when the deployment enables ROLLOUT_ROUTING
                    **_rollouts(settings, connect),
                    # G4F (WR-G4F-1): only when the deployment enables the feedback route
                    # WR-C6-CAPTURE: consent, capture and shipping, only with TRACE_PUMPS
                    **trace_capture.adapters(settings, connect),
                    **({"feedback": _pg_feedback(connect)}
                       if settings.deployment.feedback_api else {}),
                    # AP-03 (WR-AP03-3): only when the deployment enables CONSOLE_ACTIONS_API
                    **({"console_actions": ConsoleActions(connect)}
                       if settings.deployment.console_actions_api else {}),
                    # AP-07a (W1): only when the deployment enables CONSOLE_DATA_USE
                    **({"data_use": DataUse(connect)}
                       if settings.deployment.console_data_use else {}),
                    # AP-01 (WR-AP01-1): only when the deployment enables the identity routes
                    **_identity(settings, connect),
                    **adapters}
    return adapters


class NoShadows:
    """R1's `ShadowRunner` until provider-funded shadow execution exists (WR-R1-3-Cb): a
    duplicate is refused inside R1 (counted `shadow_failed`), never run, never charged."""

    async def run(self, release, serving_ref, request):
        raise errors.DependencyUnavailable("provider-funded shadow execution is not wired")


def _rollouts(settings, connect) -> dict:
    """WR-R1-3-C: R1's router over D9 (`PgRoutingReleases`) on this pool, only when
    `ROLLOUT_ROUTING` is on, and only on the `infrx_runtime` login (SR-R1-1's functions are
    EXECUTE infrx_runtime only; never service_role)."""
    if not settings.deployment.rollout_routing:
        return {}
    if login_user(settings.pilot.database_url) != "infrx_runtime":
        raise RuntimeMisconfigured(runtime_mode(settings), detail="ROLLOUT_ROUTING needs "
                                   "DATABASE_URL to log in as infrx_runtime")
    from ..rollouts.routing import Router
    from ..state.lab_rollout import PgRoutingReleases
    return {"rollouts": Router(PgRoutingReleases(connect), NoShadows())}


def _identity(settings, connect) -> dict:
    """WR-AP01-1: AP-01's identity store and L2's `LabAccess` on this pool, only when
    `IDENTITY_API` is on."""
    if not settings.deployment.identity_api:
        return {}
    from ..console.session import PgIdentity
    from ..lab.access import LabAccess
    from ..state.lab_access import PgAccessStore
    return {"identity": PgIdentity(connect), "lab_access": LabAccess(PgAccessStore(connect))}


def _web_origins(deployment) -> tuple[str, ...]:
    return tuple(o.strip() for o in deployment.web_origins.split(",") if o.strip())


def _actors(rt, identity, lab_access):
    """WR-AP01-1: `rt.actors` - the verified Supabase session (the Lab's own session check,
    `GoTrueSessions`) and, at the operator door, an operator-audience key (`AuthResolver`)."""
    if identity is None or lab_access is None:
        raise RuntimeMisconfigured(rt.mode, detail="IDENTITY_API needs AP-01's identity store "
                                                   "and L2's access: identity, lab_access")
    import httpx

    from ..auth.context import AuthResolver
    from ..console.session import SessionActors
    from .lab_auth import GoTrueSessions
    # ponytail: process-lifetime client, as in `compose.lab_surfaces`.
    sessions = GoTrueSessions(httpx.AsyncClient(base_url=rt.settings.supabase_url,
                                                timeout=httpx.Timeout(5, connect=2)),
                              rt.settings.supabase_key)
    return SessionActors(sessions, identity, keys=AuthResolver(rt),
                         origins=_web_origins(rt.settings.deployment))


def _auth_facade(rt):
    """WR-AP01-1: the auth facade on the project's publishable key, or a refusal to start."""
    deployment = rt.settings.deployment
    if not deployment.supabase_anon_key.strip():
        raise RuntimeMisconfigured(rt.mode, ("SUPABASE_ANON_KEY",),
                                   detail="AUTH_FACADE needs SUPABASE_ANON_KEY")
    import httpx

    from ..auth_facade import AuthFacade
    return AuthFacade(httpx.AsyncClient(base_url=rt.settings.supabase_url,
                                        timeout=httpx.Timeout(10, connect=2)),
                      deployment.supabase_anon_key, origins=_web_origins(deployment),
                      captcha_required=deployment.auth_captcha_required)


def _pg_feedback(connect):
    """D6F's `PgFeedbackService` on the api channel (G4F's route)."""
    from ..state.feedback import PgFeedbackService
    return PgFeedbackService(connect)


def _pg_lifecycle(connect, settings):
    """D10's `PgLifecycle`: the upload ticket authority and the content lifecycle (M5).
    WR-P25-1: `upload_complete` and every source/payload `MediaUploads` registers take the
    deployment's collection grace (P-25: `RETENTION_GRACE_S`), as the worker's do."""
    from ..state.lifecycle import PgLifecycle
    return PgLifecycle(connect, limits=settings.pilot,
                       grace_s=settings.deployment.retention_grace_s)


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
    capture: Any = None                       # WR-C6-CAPTURE: its ship pump (TRACE_PUMPS)
    tasks: list = field(default_factory=list)


def build_info(rt) -> None:
    """E4B's served-build check: `infrx_build_info{revision, image} 1` on /metrics, from the
    settings the installer wrote (`INFRX_RELEASE_SHA`, `INFRX_IMAGE`). A pilot refuses to
    start without them; dev/test set the gauge only when both are given."""
    deployment = rt.settings.deployment
    missing = [env_name(name) for name in ("infrx_release_sha", "infrx_image")
               if not getattr(deployment, name)]
    if missing:
        if rt.mode == "pilot":
            raise RuntimeMisconfigured(rt.mode, missing)
        return
    if getattr(rt, "metrics", None) is None:
        rt.metrics = Registry("gateway")
    rt.metrics.set("infrx_build_info", 1, revision=deployment.infrx_release_sha,
                   image=deployment.infrx_image)


def build_ingress_deps(rt, *, catalog=None, stream=None, objects=None, jobs=None, index=None,
                       pool=None, consent_for=None, attachments=None,
                       lifecycle=None, readiness=None, feedback=None, lab_control=None,
                       lab_traces=None, rollouts=None, trace_export=None, lab_evaluations=None,
                       lab_pipelines=None, lab_releases=None, lab_checkpoints=None,  # noqa: F811
                       lab_datasets=None, capture=None, console_actions=None,
                       data_use=None, identity=None, lab_access=None) -> IngressDeps:
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
    readiness, expectation = admission_readiness(rt, jobs, readiness)
    reconciler = Reconciler(store=jobs, index=index if index is not None else valkey_index(pilot),
                            now=_utc_now, worker_id=f"gateway-{uuid.uuid4().hex[:8]}")
    relay = Relay(jobs=jobs, stream=stream, media=None, regime=deployment.accounting_regime,
                  catalog=catalog, active_rate_card_version=pilot.active_rate_card_version,
                  limits=pilot, clock=rt.clock, registry=rt.metrics, readiness=readiness,
                  expectation=expectation)
    relay.media = rt.media_store = MediaUploads(
        objects, cache=ProcessingCache(pilot.processing_cache_dir,
                                       ttl_s=pilot.processing_cache_ttl_s),
        limits=pilot, fetcher=fetch.MediaFetcher(pilot, allowed_mime=settings.allowed_video_mime),
        job_org=relay.job_org, attachments=attachments, uploads=lifecycle, content=lifecycle)
    rt.large_bodies = intake.LargeBodies(limit=deployment.large_body_limit,
                                         threshold=deployment.large_body_threshold_bytes)
    checks = {"price_source": Probe(models.price_check(catalog, settings)),
              "journal": Probe(journal_check(stream))}
    if deployment.rollout_routing:
        # R1 (WR-R1-1): the rollout router around admission, for the ingress and the jobs
        # route alike (both call `relay.accept`). Off, the relay's own accept serves.
        if rollouts is None:
            raise RuntimeMisconfigured(rt.mode, detail="ROLLOUT_ROUTING needs the rollout "
                                                       "router over D9's release store: rollouts")
        from ..rollouts import routing
        relay.accept = routing.hook(relay.accept, rollouts)
    rt.relay = relay
    # G4F (WR-G4F-1): the feedback route mounts over this, and only when enabled.
    rt.feedback = feedback if deployment.feedback_api else None
    # G4T (WR-G4T-1): the trace export mounts over this, and only when enabled.
    rt.trace_export = _trace_export(rt, trace_export) if deployment.trace_export_api else None
    # LAB-API (WR-LAB-API-1): each Lab surface mounts over these, and only when enabled.
    rt.lab_control = lab_control if deployment.lab_control else None
    rt.lab_traces = lab_traces if deployment.lab_traces else None
    rt.lab_evaluations = lab_evaluations if deployment.lab_evals else None
    rt.lab_pipelines = lab_pipelines if deployment.lab_pipelines else None
    rt.lab_releases = lab_releases if deployment.lab_releases else None
    rt.lab_checkpoints = lab_checkpoints if deployment.lab_checkpoints else None
    rt.lab_datasets = lab_datasets if deployment.lab_datasets else None
    # AP-01 (WR-AP01-1): the web API's actor source and identity routes, only when enabled;
    # off, `rt.actors` stays whatever the composition already put there (None by default).
    rt.identity = identity if deployment.identity_api else None
    rt.lab_access = lab_access if deployment.identity_api else None
    rt.actors = (_actors(rt, identity, lab_access) if deployment.identity_api
                 else getattr(rt, "actors", None))
    rt.auth_facade = _auth_facade(rt) if deployment.auth_facade else None
    # AP-02 (WR-AP02-1): the console reads mount over this, and only when enabled.
    rt.console_reads = _console_reads(rt, deployment) if deployment.console_reads else None
    # AP-03 (WR-AP03-3): the console/operator mutations mount over this, only when enabled,
    # and never without the session actors (a route that answers nobody).
    if deployment.console_actions_api and rt.actors is None:
        raise RuntimeMisconfigured(rt.mode, ("SESSION_ACTORS",),
                                   detail="CONSOLE_ACTIONS_API needs AP-01's session actors")
    rt.console_actions = console_actions if deployment.console_actions_api else None
    # AP-07a (W1): the grantor's data-use routes mount over this, only when enabled, and
    # never without the session actors.
    if deployment.console_data_use and rt.actors is None:
        raise RuntimeMisconfigured(rt.mode, ("SESSION_ACTORS",),
                                   detail="CONSOLE_DATA_USE needs AP-01's session actors")
    rt.data_use = data_use if deployment.console_data_use else None
    rt.lifetime = Lifetime(probes=tuple(checks.values()), reconciler=reconciler, pool=pool,
                           relay=relay, capture=capture)
    return IngressDeps(accept=relay.accept, checks=checks, consent_for=consent_for,
                       capture=capture,
                       catalog=catalog, large_bodies=rt.large_bodies)


def _console_reads(rt, deployment):
    """AP-02's `ConsoleReads` on its own login (`set_role=False`: each read does `SET LOCAL
    ROLE authenticated` itself, safe on the transaction pooler). Enabled without the session
    actors (AP-01), the DSN or a 16-byte cursor key is a refusal to start, never a route
    that answers nobody."""
    from ..console.reads import ConsoleReads
    from ..state.jobstore import connector
    if rt.actors is None:
        raise RuntimeMisconfigured(rt.mode, ("SESSION_ACTORS",),
                                   detail="CONSOLE_READS needs AP-01's session actors")
    secret = deployment.console_cursor_secret.encode()
    if not deployment.console_database_url or len(secret) < 16:
        raise RuntimeMisconfigured(rt.mode, ("CONSOLE_DATABASE_URL", "CONSOLE_CURSOR_SECRET"))
    return ConsoleReads(connector(deployment.console_database_url, set_role=False), secret)


def _trace_export(rt, injected):
    """C2's `OwnedExport` over ClickHouse (or a test's). Enabled without ClickHouse is a
    refusal to start, never a silently missing route."""
    from ..content import build_export
    export = injected if injected is not None else build_export(rt.settings.pilot)
    if export is None:
        raise RuntimeMisconfigured(rt.mode, ("CLICKHOUSE_URL",),
                                   detail="TRACE_EXPORT_API needs CLICKHOUSE_URL")
    return export


def admission_readiness(rt, jobs, readiness):
    """W5 wiring 4: the relay's `(readiness, expectation)`. `readiness` is the
    `ReadinessStore` every admission writes its execution-ready marker through (D10's
    `PgLifecycle` on the job store's pool, from `adapters_from_env`); the expectation is this
    deployment's regime and, for CREDIT, its approved card (`ACTIVE_RATE_CARD_VERSION`, R69).
    Fail closed: a PostgreSQL job store without one refuses to start in every mode (its
    worker prepares only marked jobs). ponytail: a composition over the contract fakes with
    no readiness store keeps the pre-D10 `jobs.admit` door (fake-backed tests)."""
    if readiness is None:
        if isinstance(jobs, PgJobStore):
            raise RuntimeMisconfigured(rt.mode, detail="a PostgreSQL job store needs its "
                                                       "ReadinessStore (D10): readiness")
        return None, None
    if not isinstance(readiness, ReadinessStore):
        raise RuntimeMisconfigured(rt.mode, detail="readiness is not a ReadinessStore (D10)")
    regime = rt.settings.deployment.accounting_regime
    card = rt.settings.pilot.active_rate_card_version
    try:
        expectation = AdmissionExpectation(
            accounting_regime=regime, rate_card_version=card if regime == "credit" else None)
    except ValueError:
        raise RuntimeMisconfigured(rt.mode, ("ACTIVE_RATE_CARD_VERSION",)) from None
    return readiness, expectation


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
    if lifetime.capture is not None:          # WR-C6-CAPTURE (c): only the gateway ships
        lifetime.tasks.append(asyncio.create_task(lifetime.capture.pump(stop)))
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*lifetime.tasks, return_exceptions=True)
        if lifetime.capture is not None:
            await lifetime.capture.close()          # flushed and sealed for the next boot
        if lifetime.relay is not None:
            await lifetime.relay.drain(DRAIN_S)     # before the pool it needs is closed
        if lifetime.pool is not None:
            await lifetime.pool.close()
