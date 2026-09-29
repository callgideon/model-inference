"""I2B-R4: `python -m infrx.worker` - the pilot's worker process (`infrx-worker.service`).

    python -m infrx.worker          # the environment: the gateway's env file (08 §5)

The composition root around W3's `WorkerService`, built from the settings the gateway reads
and by the gateway's own helpers (`gateway.pilot`: the pool, the object store, the Valkey
index, the build-info gauge), so the two processes cannot disagree about a store.

* **Fail closed.** `validate_runtime` first (an unset or unknown `INFRX_MODE`, a pilot
  without its identity or metering settings, a bad deployment value), then what this process
  needs in every mode: `DATABASE_URL` (D's `PgJobStore` and `PgStreamStore` on one pool -
  never an in-memory store), `VALKEY_URL` (Q's index), `PROCESSING_CACHE_DIR` (an absolute,
  writable directory: preparation materializes the media there, and M's `local_uri` finds
  a file another worker process prepared on disk) and `S3_MEDIA_BUCKET` (answering
  HeadBucket, as the gateway requires). A refusal names the setting, never a value, and
  exits 2 before a listener is bound or a job claimed.
* **The CREDIT regime's work doors** (W request 5, D5): `CreditWork` routes the runner's
  `load_work`/`complete` to `load_work_credit`/`complete_credit`.
* **Readiness and metrics** on `127.0.0.1:WORKER_HEALTH_PORT` (8002: what install.sh's
  `wait_ready` and 60-verify-local.sh probe). The listener is bound only after the pool has
  opened, the bucket has answered and the cache directory was found; `/readyz` is then 200
  only while the engine's `/health` answers and the pool runs (W3). `/metrics` carries
  `infrx_build_info{revision, image}` from `INFRX_RELEASE_SHA`/`INFRX_IMAGE` (a pilot
  refuses to start without them; never read from git or docker).
* **SIGTERM or SIGINT: W3's drain.** Stop claiming, let in-flight attempts finish within the
  generation budget (the unit's `docker stop -t 330` covers it), release the rest to the
  store, then exit 0. A runner or the reaper that died ends the process non-zero, for the
  unit's `Restart=always`.

* **Preparation** (PREP-WORKER): a second pool on `prepare_dispatch` candidates
  (`preparation.PreparationRunner`, `PREPARATION_CONCURRENCY` runners) claims each
  admitted job's preparation lease, prepares its media through M's `MediaPreparation`
  (the attach D2's tables record, the object store, the shared processing cache), counts
  its prompt with the engine's own `/tokenize` and queues it with `prepared(...,
  prompt_tokens=)`; the inference pool then runs it. Same index, same stores, same drain.

* **Housekeeping** (M6 wiring 1 + 2, E3C F-4): this process is the ONE long-lived owner
  of collection - gateways run none. Exactly one `RetentionCollector.run` over the
  configured `PgLifecycle` and the media store (`RETENTION_INTERVAL_S`), one cache keeper
  (`ProcessingCache.sweep`, `CACHE_SWEEP_INTERVAL_S`) and one journal prune
  (`PgStreamStore.expire`, `JOURNAL_EXPIRE_INTERVAL_S`), all on the worker's `Registry`.
  Preparation registers its artifacts with the same lifecycle (`content=`), the cache is
  bounded by `PROCESSING_CACHE_MAX_BYTES`, and the engine pins every file it opens from
  submit to terminal (a keeper without the pin could remove an input mid-attempt).

* **Trace pumps** (WR-T-4, `TRACE_PUMPS`, off by default): T3's expire-then-sweep over the
  shipper's retention and T2F's feedback projection on this process's pool. WR-C6-CAPTURE
  (c): the gateway owns `TRACE_SPOOL_DIR` and ships (its lifespan, `gateway.capture`); this
  process spools each consented async job's output under `TRACE_SPOOL_DIR/jobs` through its
  runner (`capture.capture_jobs`) and never locks the gateway's directory.
* **Lab evaluation** (WR-B-5, `LAB_EVAL_WORKER`, off by default, Lab-only): D2's
  `OutboxRelay` over D7's Lab outbox with `EvalRuns` as its scheduler, and `lab_recover` on
  a timer. It refuses to start until an evaluator source and a dev target source exist.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from ..config import RuntimeMisconfigured, from_env, runtime_mode, validate_runtime
from ..evaluation import runner as evaluation
from ..contracts import errors
from ..contracts.records import OutboxKind
from ..contracts.v2.money_units import CREDIT_REGIME
from ..gateway import capture, pilot
from ..media import fetch
from ..media.attachments import PgAttachments
from ..media.prepare import MediaPreparation, ProcessingCache
from ..media.retention import RetentionCollector
from ..observe.metrics import Registry
from ..state.jobstore import PgJobStore, PreparedWork, connector
from ..state.journal import PgStreamStore
from ..state.lab_data import PgLabDataStore
from ..state.lifecycle import PgLifecycle
from ..state.outbox import OutboxRelay
from ..traces import ship
from ..traces.feedback import FeedbackProjector
from ..traces.feedback.pg import PgFeedbackOutbox
from .attempt import AttemptRunner
from .engine import VllmEngine
from .loop import WorkerLoop
from .preparation import PreparationRunner
from .service import PgReconciliation, WorkerService

log = logging.getLogger("infrx.worker")

#: vLLM's `--served-model-name` (models/marlin2b/serve.sh).
SERVED_MODEL = "marlin2b"
#: A startup refusal, as preflight's: the configuration cannot serve.
REFUSED = 2


class Wall:
    """The worker's clock (the store fences on the database clock regardless)."""

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


class CreditWork:
    """W request 5 (D5): W's runner calls `load_work`/`complete`; a CREDIT job's doors are
    `load_work_credit`/`complete_credit`. Every other call is the store's own."""

    def __init__(self, store) -> None:
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def load_work(self, lease):
        work = await self.store.load_work_credit(lease)
        # A CREDIT job has no USD price snapshot, and W's runner reads none: the v1 record
        # is built without it rather than with an invented one.
        return PreparedWork.model_construct(
            request=work.request.request, media_refs=work.media_refs,
            prepared_refs=work.prepared_refs, budgets=work.budgets,
            prompt_tokens=work.prompt_tokens,
            serving_version_id=work.request.pins.serving_version_id)

    async def complete(self, lease, outcome):
        settled, _settlement = await self.store.complete_credit(lease, outcome)
        return settled


def compose(settings, *, objects=None, index=None, evaluators=None, targets=None):
    """`(service, pool)` from settings. `objects`/`index` are for tests only, as in
    `create_app`; the stores are always PostgreSQL. `evaluators`/`targets` are the Lab
    evaluation worker's sources (WR-B-2(b), WR-B-3), which nothing composes yet. Raises
    `RuntimeMisconfigured` naming the setting that cannot serve. Nothing here connects
    except the bucket's HeadBucket (and ClickHouse, only when `TRACE_PUMPS` is on)."""
    mode = validate_runtime(settings)
    limits, deployment = settings.pilot, settings.deployment
    missing = [name for name, value in (("DATABASE_URL", limits.database_url),
                                        ("VALKEY_URL", limits.valkey_url),
                                        ("PROCESSING_CACHE_DIR", limits.processing_cache_dir))
               if not value.strip()]
    if missing:
        raise RuntimeMisconfigured(mode, missing)
    root = limits.processing_cache_dir
    if not (os.path.isabs(root) and os.path.isdir(root)
            and os.access(root, os.R_OK | os.W_OK | os.X_OK)):
        raise RuntimeMisconfigured(
            mode, detail="PROCESSING_CACHE_DIR must be an absolute path to a writable directory")
    rt = SimpleNamespace(settings=settings, mode=mode, metrics=Registry("worker"))
    pilot.build_info(rt)                  # the gateway's gauge, from the same two settings
    objects = objects if objects is not None else pilot.object_store(settings)
    pool, connect = pilot.connection_pool(settings)
    store = PgJobStore(connect, limits=limits)
    jobs = CreditWork(store) if deployment.accounting_regime == CREDIT_REGIME else store
    # --- M6-WIRING (wiring 1): the lifecycle, the bounded cache, the metrics -------------
    lifecycle = PgLifecycle(connect, limits=limits,       # claim TTL 300 s > the 75 s delete
                            grace_s=deployment.retention_grace_s)     # P-25: 3,600 s
    media = MediaPreparation(objects, limits=limits, content=lifecycle,
                             cache=ProcessingCache(root, ttl_s=limits.processing_cache_ttl_s,
                                                   max_bytes=deployment.processing_cache_max_bytes,
                                                   metrics=rt.metrics))
    journal = PgStreamStore(connect, limits=limits)
    # --- end M6-WIRING (wiring 1) --------------------------------------------------------
    media.attachments = PgAttachments(connect)    # R99 (c): the attach `prepare` reads
    engine = VllmEngine(httpx.AsyncClient(base_url=settings.upstream,
                                          timeout=httpx.Timeout(600, connect=10)),
                        served_model=SERVED_MODEL, clock=Wall, limits=limits,
                        media_settings=settings, local_uri=media.local_uri,
                        # M6-WIRING (wiring 2): held from submit to terminal
                        pin=media.cache.pin, reprepare=reprepare_with(media))
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    runner = AttemptRunner(jobs=jobs, stream=journal,
                           engine=engine, clock=Wall, worker_id=worker_id,
                           count_prompt_tokens=lambda work: work.prompt_tokens,
                           put_result=store.put_result, limits=limits)
    if deployment.trace_pumps:                           # WR-C6-CAPTURE (c), off by default
        capture.capture_jobs(runner, limits.trace_spool_dir, Wall, limits=limits)
    scheduler = index if index is not None else pilot.valkey_index(limits)
    loop = WorkerLoop(scheduler=scheduler, runner=runner, worker_id=worker_id, limits=limits,
                      metrics=rt.metrics)     # E1B WR-4: each attempt's phase timings
    preparation = WorkerLoop(
        scheduler=scheduler, worker_id=worker_id, kind=OutboxKind.prepare_dispatch,
        runner=PreparationRunner(jobs=jobs, media=media, engine=engine, worker_id=worker_id,
                                 limits=limits, readiness=lifecycle),
        limits=limits)
    chores = housekeeping(deployment, lifecycle, objects, media, journal, rt.metrics)
    if deployment.trace_pumps:                           # WR-T-4, off by default
        chores |= trace_pumps(settings, mode, connect)
    if deployment.lab_eval_worker:                       # WR-B-5, off by default
        chores |= lab_eval(mode, connect, objects, evaluators, targets, worker_id)
    service = WorkerService(loop=loop, jobs=jobs, engine=engine,
                            concurrency=limits.worker_concurrency,
                            health_port=deployment.worker_health_port,
                            reconciliation=reconciliation_reader(deployment),
                            metrics=rt.metrics, pool=pool, preparation=preparation,
                            preparation_concurrency=limits.preparation_concurrency,
                            housekeeping=chores)
    return service, pool


# --- W5-F5 (E3C F-6): the reconciliation gauges on D10's monitor login -------------------
def reconciliation_reader(deployment):
    """S3 F4's reader on `MONITOR_DATABASE_URL` (0021 `infrx_monitor`), or None. 0021 grants
    the reconciliation views to the monitor role only (0021:550); the runtime login is never
    granted them (R122-R127), so on the worker's own pool every tick was refused. Unset:
    no gauges, said once here - never an error per tick."""
    dsn = deployment.monitor_database_url
    if not dsn:
        log.info("reconciliation gauges disabled: no monitor login")
        return None
    # ponytail: one connection per tick (every 10 s), bounded by the login's own
    # statement_timeout; put `connect_timeout` in the DSN if a hung connect ever matters.
    return PgReconciliation(connector(dsn, set_role=not pilot.dedicated_login(dsn)))
# --- end W5-F5 ----------------------------------------------------------------------------


# --- M6-WIRING: the worker's housekeeping (wiring 1 + E3C F-4) ---------------------------
def housekeeping(deployment, lifecycle, objects, media, journal, metrics) -> dict:
    """The three loops the worker - and only the worker - runs, by task name."""
    collector = RetentionCollector(lifecycle, objects, page_size=100, concurrency=8)
    return {
        "retention": lambda: collector.run(deployment.retention_interval_s, metrics=metrics),
        "cache_keeper": lambda: every(deployment.cache_sweep_interval_s,
                                      lambda: asyncio.to_thread(media.cache.sweep),
                                      "cache sweep"),
        "journal_expire": lambda: every(deployment.journal_expire_interval_s,
                                        lambda: expire_journal(journal), "journal expire"),
    }


async def every(interval_s: float, step, what: str, *, sleep=asyncio.sleep) -> None:
    """`await step()` every `interval_s`, forever; a failed step is logged and retried."""
    while True:
        try:
            await step()
        except Exception:
            log.exception("%s failed", what)
        await sleep(interval_s)


async def expire_journal(journal) -> int:
    """One prune pass: `expire` until nothing past `JOURNAL_CHUNK_TTL_S` is left (each call
    takes at most `EXPIRE_JOBS_PER_PASS` jobs; a job locked by an append is the next pass's)."""
    removed = 0
    while found := await journal.expire():
        removed += found
    return removed


# --- WR-T-4 / WR-B-5 (composition lane, LW2): the trace pumps and the Lab eval worker ----
# ponytail: fixed cadences; deployment settings when a measured backlog asks for them.
FEEDBACK_PROJECTION_S = 10.0     # T3's alert fires at 15 min of projection lag
TRACE_RETENTION_S = 300.0        # the media retention's cadence (P-25)
LAB_PUMP_S = 5.0
LAB_RECOVER_S = 30.0             # = the lease below: an expired lease waits at most one more
LAB_EVAL_LIMITS = evaluation.Limits(lease_s=30, max_attempts=3, dispatch_retries=2,
                                    concurrency=2)


def trace_pumps(settings, mode, connect) -> dict:
    """T3's WR-T-4 calls, by task name: the retention `ship.build_shipper` composes and T2F's
    projector into that retention's projection. No spool and no ship step here
    (WR-C6-CAPTURE (c)): the gateway owns `TRACE_SPOOL_DIR` and ships it."""
    limits = settings.pilot
    missing = [name for name, value in (("TRACE_SPOOL_DIR", limits.trace_spool_dir),
                                        ("CLICKHOUSE_URL", limits.clickhouse_url),
                                        ("S3_TRACE_BUCKET", limits.s3_trace_bucket))
               if not value.strip()]
    if missing:
        raise RuntimeMisconfigured(mode, missing)
    holds = content_holds(mode, connect)                                # WR-C2-2
    try:
        shipper = ship.build_shipper(limits, None,
                                     endpoint_url=settings.deployment.s3_endpoint_url,
                                     holds=holds)                       # WR-C2-2b
    except Exception as failure:          # noqa: BLE001 - every failure refuses startup
        raise RuntimeMisconfigured(mode, detail="TRACE_PUMPS: the spool or CLICKHOUSE_URL "
                                   f"did not answer ({type(failure).__name__})") from None
    retention = shipper.retention
    projector = FeedbackProjector(PgFeedbackOutbox(connect), retention.feedback,
                                  retention=retention)

    async def retain():
        await retention.expire()
        return await retention.sweep()
    return {"trace_retention": lambda: every(TRACE_RETENTION_S, retain, "trace retention"),
            "feedback_projection": lambda: every(FEEDBACK_PROJECTION_S, projector.pump,
                                                 "feedback projection")}


def content_holds(mode, connect):
    """WR-C2-2: T3's sweep keeps an object a live C2 content ref still holds - C2's
    `ContentAccess.holds` over 0041's refs (lab-sql-lw3) on this pool. Without them in the
    build the pumps refuse by name. ponytail: drop the refusal once #16 is on every base."""
    from ..content import ContentAccess
    try:
        from ..state.lab_content import PgContentRefs
    except ImportError:
        raise RuntimeMisconfigured(mode, detail="TRACE_PUMPS needs C2's content refs "
                                                "(0041)") from None
    return ContentAccess(PgContentRefs(connect), None).holds       # `holds` reads refs only


def lab_eval(mode, connect, objects, evaluators, targets, worker_id) -> dict:
    """B1's WR-B-5: D7's outbox pumped into `EvalRuns`, and `lab_recover` on a timer.
    The Lab's objects are the media store's (`lab/<provider>/...` keys, N1's WR-N-3)."""
    if evaluators is None or targets is None:
        raise RuntimeMisconfigured(mode, detail="LAB_EVAL_WORKER needs an evaluator source "
                                   "(WR-B-2(b)) and a dev target source (WR-B-3)")
    store = PgLabDataStore(connect)
    # ponytail: the pump awaits each run, so a run longer than `redelivery_s` is handed to
    # another worker process too; D7's leases make that a duplicate delivery (B1's drill).
    relay = OutboxRelay(Kinds(store, ("eval_run",)),
                        EvalRuns(store, objects, evaluators, targets,
                                 worker_id=f"{worker_id}-lab"),
                        worker_id=f"{worker_id}-lab-relay")
    return {"lab_eval": lambda: every(LAB_PUMP_S, relay.pump, "lab eval"),
            "lab_recover": lambda: every(LAB_RECOVER_S, store.recover, "lab recover")}


class Kinds:
    """R215 / WR-LSQ-C2B: D7's outbox as one role's relay sees it - `dispatch_pending` claims
    only `kinds` (0050), so a role never claims, refuses and redelivers another's events."""

    def __init__(self, store, kinds) -> None:
        self.store, self.kinds = store, tuple(kinds)

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def dispatch_pending(self, **kw):
        return await self.store.dispatch_pending(**kw, kinds=self.kinds)


RUN_FINAL = ("succeeded", "failed", "cancelled")


class EvalRuns:
    """The Lab outbox's `eval_run` handler (`OutboxRelay`'s scheduler). The event names a
    run D7 already created (`lab_create_run` wrote it), so a delivery - first or again -
    rebuilds it with `resume`, never `freeze`: after a revocation `freeze` is refused and the
    run would never end (B1's recheck). Only a finished, cancelled or budget-stopped run is
    acknowledged; a wallet stop or a run still unfinished raises (the relay hands it out
    again after its window); any other kind is not this handler's."""

    def __init__(self, store, objects, evaluators, targets, *, worker_id: str) -> None:
        self.store, self.objects, self.worker_id = store, objects, worker_id
        self.evaluators, self.targets = evaluators, targets    # ref -> spec / (endpoint, rev)

    async def enqueue(self, event) -> bool:
        if event.kind != "eval_run":
            raise errors.InvalidRequest(f"no Lab worker handles {event.kind}")
        provider, run_id = event.provider_org_id, event.payload["run_id"]
        status = await self.store.run_status(run_id, provider_org_id=provider)
        record = await self.store.resolve(status["run_ref"], provider_org_id=provider)
        if any(ref.split(":")[2:3] != [provider]            # R167: never another provider's
               for ref in (record.evaluator_ref, record.serving_ref)):
            raise errors.NotFound(f"eval run {run_id} names another provider's ref")
        endpoint, deployment = await self.targets(record.serving_ref)
        frozen = await evaluation.resume(self.store, run_id,
                                         evaluator=await self.evaluators(record.evaluator_ref),
                                         provider_org_id=provider)
        report = await evaluation.Runner(self.store, self.objects, endpoint, deployment,
                                         worker_id=self.worker_id,
                                         limits=LAB_EVAL_LIMITS).run(frozen)
        if report["stopped"] == "wallet_exhausted":
            raise errors.InsufficientCredit(f"eval run {run_id}: the provider_dev wallet")
        if report["stopped"] == "budget_exhausted" or report["state"] in RUN_FINAL:
            return True
        # Nothing pending, but a dead attempt's cases stay `leased` until `lab_recover`
        # reaps them and recover emits no event: acknowledging now would orphan the run.
        raise errors.ResultPending(f"eval run {run_id} is still {report['state']}")
# --- end WR-T-4 / WR-B-5 ------------------------------------------------------------------


def reprepare_with(media):
    """M6 wiring 2: a pinned input found gone is prepared again, from the durable record."""
    async def reprepare(job_id: str, profile: str) -> None:
        await media.prepare(job_id, profile)
        media.prepared_by_job.pop(job_id, None)   # nothing in this process reads it (F3)
    return reprepare
# --- end M6-WIRING ------------------------------------------------------------------------


async def run(settings, service, pool) -> int:
    """Open the pool (a refusal if it cannot), serve until a signal or a death, drain."""
    try:
        await pool.open(wait=True, timeout=settings.deployment.database_pool_connect_timeout_s)
    except Exception as failure:          # noqa: BLE001 - every failure refuses startup
        await pool.close()
        print(f"infrx.worker: refusing to start: INFRX_MODE={runtime_mode(settings)!r}: "
              f"DATABASE_URL did not answer ({type(failure).__name__})", file=sys.stderr)
        return REFUSED
    try:
        await service.serve()
    finally:
        await pool.close()
        await service.engine.client.aclose()
    return 1 if service.loop.failures or service._died() else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    fetch.silence_transport_logs()        # after basicConfig: no URL reaches the log
    try:
        settings = from_env()
        service, pool = compose(settings)
    except ValueError as refused:         # RuntimeMisconfigured included: names, no values
        print(f"infrx.worker: refusing to start: {refused}", file=sys.stderr)
        return REFUSED
    return asyncio.run(run(settings, service, pool))


if __name__ == "__main__":
    raise SystemExit(main())
