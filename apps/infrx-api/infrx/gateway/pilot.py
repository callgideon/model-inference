"""G2 item 5: the pilot composition - `IngressDeps` built from the real adapters.

    rt.ingress = pilot.build_ingress_deps(rt, catalog=..., stream=..., objects=...)
    app = FastAPI(lifespan=pilot.lifespan, ...)

What `create_app` calls at the cutover, once per process:

* `PgJobStore` over a psycopg pool whose connections `set role service_role` (D2 request 7;
  never on 0021's dedicated logins, R127) and carry the deployment's statement timeout;
* the `Relay` (G2's acceptor, sync wait and SSE relay), dispatching admission on
  `ACCOUNTING_REGIME` and holding the CREDIT pins to `ACTIVE_RATE_CARD_VERSION`;
* one `MediaUploads` (M3 request 3), exposed as `rt.media_store` - the instance G4U's upload
  router finalizes into - and one `LargeBodies`, `rt.large_bodies`, shared with it;
* the Q3 `Reconciler` over the Valkey index, run for the process lifetime by `lifespan`;
* the gateway `Registry` (I3B request 1) and the two readiness probes `REQUIRED_CHECKS`
  names, as sync callables over cached answers the lifetime task refreshes.

`adapters_from_env` is what `create_app` composes when a caller injects nothing: D5's
`PgCatalogDirectory`, D4's `PgStreamStore` and D2's `PgJobStore` (both regimes: the relay
dispatches on `ACCOUNTING_REGIME`, R86) on the one pool, and M1-L2's `S3ObjectStore` on the
bucket `S3_MEDIA_BUCKET` names - probed with HeadBucket before anything else is built. No
bucket, or one that does not answer, refuses startup rather than staging into process
memory, which would make acceptance depend on gateway-local bytes (02 step 1).
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
from ..contracts.limits import env_name
from ..contracts.v2.lifecycle import AdmissionExpectation, ReadinessStore
from ..media import fetch
from ..media.attachments import PgAttachments
from ..media.prepare import ProcessingCache
from ..media.uploads import MediaUploads
from ..observe.metrics import Registry
from ..scheduling.reconcile import Reconciler
from ..state.catalog import PgCatalogDirectory
from ..state.jobstore import PgJobStore, session_state_allowed
from ..state.journal import PgStreamStore
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


#: 0021's dedicated logins (R127): members of no role, so they never `set role`.
DEDICATED_LOGINS = frozenset({"infrx_runtime", "infrx_monitor"})


def dedicated_login(dsn: str) -> bool:
    """True when the DSN logs in as a dedicated login - bare, or as Supavisor's
    `<role>.<project-ref>`. Every other login (bda1586's broad one) keeps D2 request 7."""
    from psycopg.conninfo import conninfo_to_dict
    return (conninfo_to_dict(dsn).get("user") or "").split(".")[0] in DEDICATED_LOGINS


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
                    **_lab(settings, connect, adapters["objects"]),
                    **_lab_checkpoints(settings, connect),
                    # R1 (WR-R1-3-C): only when the deployment enables ROLLOUT_ROUTING
                    **_rollouts(settings, connect),
                    # G4F (WR-G4F-1): only when the deployment enables the feedback route
                    **({"feedback": _pg_feedback(connect)}
                       if settings.deployment.feedback_api else {}),
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
    from psycopg.conninfo import conninfo_to_dict
    if (conninfo_to_dict(settings.pilot.database_url).get("user")
            or "").split(".")[0] != "infrx_runtime":
        raise RuntimeMisconfigured(runtime_mode(settings), detail="ROLLOUT_ROUTING needs "
                                   "DATABASE_URL to log in as infrx_runtime")
    from ..rollouts.routing import Router
    from ..state.lab_rollout import PgRoutingReleases
    return {"rollouts": Router(PgRoutingReleases(connect), NoShadows())}


def _lab(settings, connect, objects=None) -> dict:
    """LAB-API: `lab_control` / `lab_traces` for the switches that are on, over one session
    verifier (the project's auth server) and L2's `LabAccess` on this pool. L3's control
    operations are not composed until L3 merges (its routes answer 503; health is served).
    `LAB_TRACES` needs T2I's projection and trace bucket, or startup is refused."""
    deployment = settings.deployment
    teachers = _teachers(settings, connect, objects)
    if not (deployment.lab_control or deployment.lab_traces or deployment.lab_evals
            or deployment.lab_pipelines or deployment.lab_releases or deployment.lab_datasets):
        return {}
    import httpx

    from ..lab.access import LabAccess
    from ..state.lab_access import PgAccessStore
    from .lab_auth import GoTrueSessions
    from .routes.lab_control import LabControl
    # ponytail: this client lives as long as the process; close it in `lifespan` if an app is
    # ever rebuilt inside one process outside tests.
    sessions = GoTrueSessions(httpx.AsyncClient(base_url=settings.supabase_url,
                                                timeout=httpx.Timeout(5, connect=2)),
                              settings.supabase_key)
    access = LabAccess(PgAccessStore(connect))
    lab = {"lab_control": LabControl(sessions, access, lab_operations(connect, access))} \
        if deployment.lab_control else {}
    if deployment.lab_traces:
        lab["lab_traces"] = _lab_traces(settings, connect, sessions, access)
    if deployment.lab_datasets:           # WR-N4-1 over D7, L2 and the Lab objects (R182)
        from ..state.lab_data import PgLabDataStore, PgLabImportJobs
        from .routes.lab_datasets import LabDatasets
        lab["lab_datasets"] = LabDatasets(sessions, access, PgLabDataStore(connect), objects,
                                          PgLabImportJobs(connect))    # WR-C5-N4-ROUTE (0051)
    return {**lab, **_lab_2(deployment, connect, sessions, access, objects, teachers)}


def _teachers(settings, connect, objects):
    """WR-P4B-1: P2's `TeacherWiring` for the pipeline surface when `LAB_TEACHERS` is on -
    the Lab workers' one composition (`teacher_wiring`) with N2's public redaction (WR-P2-4),
    the approved rate table and the pilot settings (judge mode not live by default). Only
    beside `LAB_PIPELINES` (it has no route of its own), and only to the local teacher fake
    `LAB_TEACHER_URL` names (J2's provider refuses any other host until P-10)."""
    deployment = settings.deployment
    if not deployment.lab_teachers:
        return None
    mode = runtime_mode(settings)
    if not deployment.lab_pipelines:
        raise RuntimeMisconfigured(mode, detail="LAB_TEACHERS needs LAB_PIPELINES (its routes "
                                                "are the pipeline surface's)")
    from ..datasets.versions import redact_content
    from ..lab.workers.__main__ import teacher_wiring
    try:
        return teacher_wiring(connect, objects, provider_url=deployment.lab_teacher_url,
                              settings=settings.pilot, redact=redact_content)
    except errors.DomainError:            # names the setting, never its value
        raise RuntimeMisconfigured(mode, detail="LAB_TEACHER_URL: teacher egress is the local "
                                                "teacher fake until P-10") from None


def _lab_checkpoints(settings, connect) -> dict:
    """WR-B3-2: B3's receiver over D8's checkpoint ledger (0042) and D7, with the key
    directory `LAB_CHECKPOINT_KEYS` names; on without a valid directory refuses to start."""
    deployment = settings.deployment
    if not deployment.lab_checkpoints:
        return {}
    from ..state.lab_data import PgLabDataStore
    from .routes.lab_checkpoints import LabCheckpoints, key_directory
    mode = runtime_mode(settings)
    try:
        keys = key_directory(deployment.lab_checkpoint_keys)
    except ValueError as refused:         # names the setting, never its value
        raise RuntimeMisconfigured(mode, ("LAB_CHECKPOINT_KEYS",), detail=str(refused)) \
            from None
    try:
        from ..state.lab_pipeline import PgCheckpointLedger
    except ImportError:                   # ponytail: until lab-sql-lw3 (#16) is on the base
        raise RuntimeMisconfigured(mode, detail="LAB_CHECKPOINTS needs D8's checkpoint "
                                                "ledger (0042)") from None
    return {"lab_checkpoints": LabCheckpoints(keys, PgCheckpointLedger(connect),
                                              PgLabDataStore(connect))}


class RunLedger:
    """P3's `PgRunLedger` (D8, 0042) as the pipeline surface's `ledger`; the run and
    checkpoint listings WR-LAB2-4 asks of lab-sql (SR-P3-1) are not written yet: 503."""

    def __init__(self, ledger) -> None:
        self.ledger = ledger

    def __getattr__(self, name):
        return getattr(self.ledger, name)

    async def run_rows(self, *args):
        raise errors.DependencyUnavailable("the run listings are not wired (WR-LAB2-4)")

    checkpoint_rows = run_rows


def _lab_2(deployment, connect, sessions, access, objects=None, teachers=None) -> dict:
    """LAB-API-2: the evaluation, pipeline and release surfaces for the switches that are on,
    over D7 (`PgLabDataStore`, merged); the pipelines over D8's label log and run ledger
    (WR-P1-D8-C / WR-P3-D8-C), the Lab objects and P3's evaluation port over B3/B1
    (WR-E7L-1) with the production suites (WR-C4-B3-SUITES: D7's receipt, D8's subscription,
    L3's dev deployer on this pool). The ports
    whose tables are not merged (experiments, the B3 ledger listing, the catalog; the run
    listings) are absent, so their routes answer 503; the release surface is WR-R4-2's
    (`lab_releases`)."""
    from ..evaluation import checkpoints
    from ..state.lab_data import PgLabDataStore
    from ..state.lab_pipeline import PgLabelLog, PgRunLedger
    from .routes.lab_evaluations import LabEvaluations
    from .routes.lab_pipelines import LabPipelines
    store = PgLabDataStore(connect)
    return {**({"lab_evaluations": LabEvaluations(sessions, access, store=store)}
               if deployment.lab_evals else {}),
            **({"lab_pipelines": LabPipelines(sessions, access, store=store, objects=objects,
                                              log=PgLabelLog(connect),
                                              ledger=RunLedger(PgRunLedger(connect)),
                                              evals=checkpoints.Evaluations(
                                                  store, objects, access,
                                                  suites=checkpoints.production_suites(
                                                      connect)),
                                              teachers=teachers)}
               if deployment.lab_pipelines else {}),
            **({"lab_releases": lab_releases(connect, sessions, access, objects)}
               if deployment.lab_releases else {})}


# --- WR-R4-2 (composition-6): the release surface's ports --------------------------------
def lab_releases(connect, sessions, access, objects):
    """`LAB_RELEASES`: the read models over D9, D7 and the Lab objects, 0043's proposals and
    D9 as the route's store, on this pool."""
    from ..state.lab_data import PgLabDataStore
    from ..state.lab_rollout import PgReleaseProposals, PgReleaseStore
    from .routes.lab_releases import LabReleases
    d9 = PgReleaseStore(connect)
    return LabReleases(sessions, access,
                       records=ReleaseRecords(d9, PgLabDataStore(connect), objects),
                       proposals=ReleaseProposals(PgReleaseProposals(connect)), store=d9)


SHOWN = ("running", "approved", "rolled_back")      # the Lab's release states (port.ts)


def _z(value) -> str:
    """A database timestamp (ISO text or datetime) as the Lab's `...Z` second."""
    from datetime import datetime, timezone
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReleaseRecords:
    """WR-R4-2: `/lab/v1/releases`' read models (port.ts, snake_case). Each D9 release (0048)
    with D7's policy revision and the plan its launcher stored (WR-C5-PLAN; none stored: a
    503 naming it, never a guessed plan); `progress` null (R1's aggregates are not readable,
    WR-C5-LIVE); the verdict is D9's latest decision. Decisions are 0053's. R3's variant
    listing is not written (WR-C6-VARIANTS): 503."""

    def __init__(self, d9, store, objects) -> None:
        self.d9, self.store, self.objects = d9, store, objects

    async def releases(self, provider_org_id: str) -> list[dict]:
        from ..lab.workers.__main__ import plan_key
        from ..rollouts.control import Plan
        out = []
        for item in await self.d9.releases_in(SHOWN, provider_org_id=provider_org_id):
            raw = await self.objects.get(plan_key(provider_org_id, item.policy_id))
            if raw is None:
                raise errors.DependencyUnavailable(
                    f"the plan of {item.policy_ref} is not stored (WR-C5-PLAN)")
            plan = Plan.model_validate_json(raw).model_dump(mode="json")
            policy = await self.store.resolve(item.policy_ref, provider_org_id=provider_org_id)
            release, d = item.release, item.latest_decision
            out.append({
                "policy_ref": item.policy_ref, "endpoint_id": policy.endpoint_id,
                "version": policy.version, "baseline_ref": policy.baseline_ref,
                "mode": policy.mode, "cohort": policy.cohort,
                "candidates": [{"serving_ref": c.serving_ref, "weight_bp": c.weight_bp}
                               for c in policy.candidates],
                "state": release.state, "fence": release.fence,
                "plan_digest": release.plan_digest,
                "plan": {**{k: plan[k] for k in ("horizon_s", "min_requests", "max_error_rate",
                                                 "max_p99_ms", "max_skew_bp",
                                                 "min_quality_coverage", "max_lag_s")},
                         "budget": {"amount": plan["budget"]["value"],
                                    "unit": plan["budget"]["unit"]}},
                "started_at": _z(release.started_at), "progress": None,
                "verdict": None if d is None else {
                    "action": d.decision, "reasons": list(d.reasons),
                    "evidence_refs": list(d.evidence_refs), "evaluated_at": _z(d.at)}})
        return out

    async def decisions(self, provider_org_id: str) -> list[dict]:
        return [{**d, "decided_at": _z(d["decided_at"])}
                for d in await self.d9.decisions(provider_org_id=provider_org_id)]

    async def variants(self, provider_org_id: str):
        raise errors.DependencyUnavailable("R3's variant listing is not wired (WR-C6-VARIANTS)")


class ReleaseProposals:
    """WR-R4-2: the route's proposals over 0043 (`PgReleaseProposals`): one pending per
    release revision (its unique partial index), filed at the shown fence by the session's
    user; an operator decides it through D9's CAS (`infrx.lab.workers rollout decide`)."""

    def __init__(self, store) -> None:
        self.store = store

    @staticmethod
    def _lab(doc: dict) -> dict:
        return {"proposal_id": str(doc["proposal_id"]), "kind": doc["kind"],
                "policy_ref": doc["policy_ref"], "fence": doc["fence"], "state": doc["state"],
                "proposed_at": _z(doc["proposed_at"]),
                "decided_at": None if doc["decided_at"] is None else _z(doc["decided_at"])}

    async def proposals(self, provider_org_id: str) -> list[dict]:
        return [self._lab(p) for p in await self.store.proposals(provider_org_id=provider_org_id)]

    async def add(self, provider_org_id: str, proposal: dict) -> dict:
        return self._lab(await self.store.propose(
            proposal["policy_ref"], provider_org_id=provider_org_id,
            proposal_id=proposal["proposal_id"], kind=proposal["kind"],
            fence=proposal["fence"], proposed_by=proposal["proposed_by"]))


def lab_control(connect, access):
    """L3's `LabControl` on this pool: the control store, A3's registry and catalog, and the
    control service's engine stand-in (a smoke is 503 until WR-L3-2)."""
    from ..lab.control import LabControl
    from ..lab.control.app import NoEngine
    from ..state.lab_control import PgControlStore
    from ..state.operations import PgRegistry
    return LabControl(access, PgControlStore(connect), PgRegistry(connect),
                      PgCatalogDirectory(connect), engine=NoEngine())


def lab_operations(connect, access):
    """WR-LAB-API-2 / WR-LSQ-9-C: L3's `Operations` for `/lab/v1/control` - the gateway's and
    the I2L control service's one composition (`infrx.lab.control.app`, WR-LAB-API-2c). Its
    listings and registration read `PgControlStore` (0044's `infrx_lab_control` reads:
    provider_servings, provider_deployments, endpoint_alias, listing_versions)."""
    from ..lab.control.operations import Operations
    from ..state.lab_control import PgControlStore
    return Operations(lab_control(connect, access), PgControlStore(connect))


def control_serving(connect, principal: str):
    """WR-R2-2's composition: R2's `ServingControl` as L3's `Serving`, acting as
    `principal` (the audited actor of every alias CAS). WR-LSQ-9-C: reads are the real
    `PgControlStore`, not a typed-503 stand-in."""
    from ..lab.access import LabAccess
    from ..lab.control.operations import Serving
    from ..operations.service import OperatorSession
    from ..state.lab_access import PgAccessStore
    from ..state.lab_control import PgControlStore
    return Serving(lab_control(connect, LabAccess(PgAccessStore(connect))),
                   PgControlStore(connect),
                   OperatorSession(ops=None, principal=principal))


def _lab_traces(settings, connect, sessions, access):
    """WR-V1M-2 over T2I's projection and T3's retention on `CLICKHOUSE_URL`, and the trace
    bucket at the shipper's prefix (`build_shipper`'s `infrx/`)."""
    limits = settings.pilot
    missing = tuple(name for name, value in (("CLICKHOUSE_URL", limits.clickhouse_url),
                                             ("S3_TRACE_BUCKET", limits.s3_trace_bucket))
                    if not value.strip())
    if missing:
        raise RuntimeMisconfigured(runtime_mode(settings), missing)
    import clickhouse_connect

    from ..media.s3 import S3ObjectStore
    from ..traces.feedback import ClickHouseFeedbackProjection
    from ..traces.retention import ClickHouseRetentionStore, Retention
    from ..traces.ship import ClickHouseProjection
    from .routes.lab_traces import ClickHouseTraceRows, LabTraces, PgServing
    client = clickhouse_connect.get_client(dsn=limits.clickhouse_url)
    objects = S3ObjectStore.connect(limits.s3_trace_bucket, "infrx/",
                                    settings.deployment.s3_endpoint_url)
    retention = Retention(ClickHouseRetentionStore(client), ClickHouseProjection(client),
                          ClickHouseFeedbackProjection(client), objects,
                          content_days=limits.trace_content_max_days,
                          metadata_months=limits.trace_metadata_months)
    return LabTraces(sessions, access, PgServing(connect), ClickHouseTraceRows(client),
                     retention)


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
                       lab_pipelines=None, lab_releases=None, lab_checkpoints=None,
                       lab_datasets=None) -> IngressDeps:
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
    rt.lifetime = Lifetime(probes=tuple(checks.values()), reconciler=reconciler, pool=pool,
                           relay=relay)
    return IngressDeps(accept=relay.accept, checks=checks, consent_for=consent_for,
                       catalog=catalog, large_bodies=rt.large_bodies)


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
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*lifetime.tasks, return_exceptions=True)
        if lifetime.relay is not None:
            await lifetime.relay.drain(DRAIN_S)     # before the pool it needs is closed
        if lifetime.pool is not None:
            await lifetime.pool.close()
