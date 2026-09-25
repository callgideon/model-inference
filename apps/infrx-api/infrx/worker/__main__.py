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
from ..contracts.records import OutboxKind
from ..contracts.v2.money_units import CREDIT_REGIME
from ..gateway import pilot
from ..media import fetch
from ..media.attachments import PgAttachments
from ..media.prepare import MediaPreparation, ProcessingCache
from ..observe.metrics import Registry
from ..state.jobstore import PgJobStore, PreparedWork
from ..state.journal import PgStreamStore
from ..state.lifecycle import PgLifecycle
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


def compose(settings, *, objects=None, index=None):
    """`(service, pool)` from settings. `objects`/`index` are for tests only, as in
    `create_app`; the stores are always PostgreSQL. Raises `RuntimeMisconfigured` naming
    the setting that cannot serve. Nothing here connects except the bucket's HeadBucket."""
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
    media = MediaPreparation(objects, limits=limits,
                             cache=ProcessingCache(root, ttl_s=limits.processing_cache_ttl_s))
    media.attachments = PgAttachments(connect)    # R99 (c): the attach `prepare` reads
    engine = VllmEngine(httpx.AsyncClient(base_url=settings.upstream,
                                          timeout=httpx.Timeout(600, connect=10)),
                        served_model=SERVED_MODEL, clock=Wall, limits=limits,
                        media_settings=settings, local_uri=media.local_uri)
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    runner = AttemptRunner(jobs=jobs, stream=PgStreamStore(connect, limits=limits),
                           engine=engine, clock=Wall, worker_id=worker_id,
                           count_prompt_tokens=lambda work: work.prompt_tokens,
                           put_result=store.put_result, limits=limits)
    scheduler = index if index is not None else pilot.valkey_index(limits)
    loop = WorkerLoop(scheduler=scheduler, runner=runner, worker_id=worker_id, limits=limits)
    preparation = WorkerLoop(
        scheduler=scheduler, worker_id=worker_id, kind=OutboxKind.prepare_dispatch,
        runner=PreparationRunner(jobs=jobs, media=media, engine=engine, worker_id=worker_id,
                                 limits=limits, readiness=PgLifecycle(connect, limits=limits)),
        limits=limits)
    service = WorkerService(loop=loop, jobs=jobs, engine=engine,
                            concurrency=limits.worker_concurrency,
                            health_port=deployment.worker_health_port,
                            reconciliation=PgReconciliation(connect),
                            metrics=rt.metrics, pool=pool, preparation=preparation,
                            preparation_concurrency=limits.preparation_concurrency)
    return service, pool


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
