"""E6L-O5 / WR-B-5 (amended) / WR-OBS-1 / WR-I6-3 / WR-I7-1: the Lab worker processes.

    python -m infrx.lab.workers <eval|checkpoints|judge|annotation|training|rollout|datasets>
    python -m infrx.lab.workers rollout emergency-rollback --policy-ref <ref> --reason <text>

What the I5/I6/I7/I2L-OBS units run (`apps/infrx-api/deploy/lab/*/infrx-lab-<role>.service`),
one role per process, each OFF until its `/etc/infrx-lab/<role>.env` exists (the unit's
`ConditionPathExists`). A role reads only its environment (the env file): `LAB_DATABASE_URL`
(the Lab's own login on the transaction pooler; stores get a connection per call) and
`LAB_WORKER_HEALTH_PORT` (the unit's), plus the role's own names below. A missing one exits 2
naming it, never a value. `/livez` (every pass running), `/readyz` (that, and the database
answers) and `/metrics` on 127.0.0.1 only; SIGTERM stops the passes and exits 0 (an
unfinished lease expires and `lab_recover` returns it); a pass that ends exits 1.

Two entry points, one composition (E6L-O5): the eval role IS `infrx.worker`'s `lab_eval`
(D7's outbox into `EvalRuns`, `lab_recover` on a timer), composed here with its two sources;
the consumer worker's `LAB_EVAL_WORKER` stays a seam that refuses without them, so Lab work
never runs in a consumer process.

* `eval`       LAB_S3_BUCKET (+ LAB_S3_ENDPOINT, LAB_S3_PREFIX), LAB_EVAL_ENDPOINT_URL (the gateway that
               meters provider_dev), LAB_EVAL_ENDPOINT_KEY (the dev endpoint's credential).
               Evaluators: D7's `lab_evaluator` for the provider the ref names (WR-COMP-1,
               0034). Targets: `DevTargets` over L3's rows (WR-COMP-2; no ControlReads needed).
* `checkpoints` B3's `on_checkpoint` per `checkpoint_received` event over D8's checkpoint
               ledger (0042); needs a registry adapter and L3's dev deployer (WR-B3-3), so it
               refuses until they exist (otherwise every checkpoint would be rejected).
* `judge`      JUDGE_PROVIDER_URL, CLICKHOUSE_URL, S3_TRACE_BUCKET (+ JUDGE_MODE, default
               dry_run, and JUDGE_LIVE_BUDGET_USD): J2's `JudgeWiring` over D6J's ledger (0036)
               and T3's retention; its pass moves silent `submitting` runs to `ambiguous`;
               `jobs["judge_report"]` is J3's report on the same ledger (WR-J3-D8-C).
* `datasets`   LAB_S3_BUCKET, CLICKHOUSE_URL, S3_TRACE_BUCKET: N3's `lineage.reconcile` for
               every provider with a lineage, every page (WR-N3-2's pull half).
* `rollout`    `emergency-rollback` (LAB_OPERATOR_ID of the invoking shell): R2's operator
               stop over D9 and L3. The pass loop refuses until its inputs are readable.
* `annotation`, `training`: no worker pass exists on this base (see `_annotation`,
               `_training`); both refuse by name.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import signal
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Any, Awaitable, Callable

import httpx

from ...config import RuntimeMisconfigured, pilot_from_env, validate_pilot
from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import Environment, Visibility
from ...evaluation import checkpoints
from ...evaluation.runner import HttpDevEndpoint
from ...state.jobstore import connector
from ...worker import __main__ as worker_main
from ...worker.__main__ import every

log = logging.getLogger("infrx.lab.workers")

ROLES = ("eval", "checkpoints", "judge", "annotation", "training", "rollout", "datasets")
REFUSED = 2
DATABASE, PORT, BUCKET = "LAB_DATABASE_URL", "LAB_WORKER_HEALTH_PORT", "LAB_S3_BUCKET"
TRACES = ("CLICKHOUSE_URL", "S3_TRACE_BUCKET")
NEEDS = {"eval": (BUCKET, "LAB_EVAL_ENDPOINT_URL", "LAB_EVAL_ENDPOINT_KEY"),
         "checkpoints": (), "judge": ("JUDGE_PROVIDER_URL", *TRACES),
         "annotation": (BUCKET,), "training": (BUCKET,), "rollout": (),
         "datasets": (BUCKET, *TRACES)}
#: The Lab objects: the media bucket's store under `lab/<provider>/` (R182), at the media
#: store's prefix - `S3_MEDIA_PREFIX`'s default unless `LAB_S3_PREFIX` names the gateway's.
LAB_PREFIX = "infrx/"
# ponytail: fixed cadences, as the consumer worker's Lab pumps.
JUDGE_PASS_S = 60.0
JUDGE_SILENT_S = 300                  # a `submitting` run silent this long is `ambiguous`
LINEAGE_PASS_S = 3600.0               # the backstop behind WR-N3-2's push tombstones
PROBE_TIMEOUT_S = 5.0


@dataclass
class Worker:
    role: str
    tasks: dict[str, Callable[[], Awaitable[Any]]]
    ready: Callable[[], Awaitable[bool]]
    wiring: Any = field(default=None)
    jobs: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)


def settings(mode: str, env, names) -> dict[str, str]:
    """The named settings, each required; the health port a port."""
    values = {name: (env.get(name) or "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeMisconfigured(mode, missing)
    if PORT in values and not (values[PORT].isdigit() and 0 < int(values[PORT]) < 65536):
        raise RuntimeMisconfigured(mode, detail=f"{PORT} must be a port")
    return values


def lab_sql(mode: str, module: str, name: str):
    """A store of lab-sql-lw3's 0041-0043 (merge batch #16). Until that merge is on this base
    the import fails and the role refuses by name instead of crashing.
    ponytail: drop the refusal once #16 is on every base."""
    try:
        return getattr(importlib.import_module(f"infrx.state.{module}"), name)
    except (ImportError, AttributeError):
        raise RuntimeMisconfigured(mode, detail=f"{name} (lab-sql-lw3, 0041-0043) is not in "
                                                "this build") from None


def lab_objects(mode: str, env):
    """The Lab objects, answering HeadBucket before anything is served."""
    from ...media.s3 import S3ObjectStore, reason
    try:
        objects = S3ObjectStore.connect(env[BUCKET], env.get("LAB_S3_PREFIX") or LAB_PREFIX,
                                        env.get("LAB_S3_ENDPOINT", ""))
        objects.probe()
    except Exception as failure:          # noqa: BLE001 - every failure refuses startup
        raise RuntimeMisconfigured(mode, detail=f"{BUCKET} did not answer HeadBucket "
                                                f"({reason(failure)})") from None
    return objects


def trace_retention(limits, endpoint_url: str, objects=None, connect=None):
    """T3's `Retention` over the trace projection and bucket, as `pilot._lab_traces`; with
    the Lab objects, a deletion pushes N3's tombstones (WR-N3-2a), writing 0041 too when
    `connect` (the datasets role's login) is given (1-C3-1)."""
    import clickhouse_connect

    from ...media.s3 import S3ObjectStore
    from ...traces.feedback import ClickHouseFeedbackProjection
    from ...traces.retention import ClickHouseRetentionStore, Retention
    from ...traces.ship import ClickHouseProjection
    client = clickhouse_connect.get_client(dsn=limits.clickhouse_url)
    return Retention(ClickHouseRetentionStore(client), ClickHouseProjection(client),
                     ClickHouseFeedbackProjection(client),
                     S3ObjectStore.connect(limits.s3_trace_bucket, "infrx/", endpoint_url),
                     content_days=limits.trace_content_max_days,
                     metadata_months=limits.trace_metadata_months,
                     deleted=None if objects is None else lineage_push(objects, connect))


def lineage_push(objects, connect=None):
    """WR-N3-2a: T3's deletion hook on the Lab's retention - N3's `lineage.tombstone` of the
    deleted request for every provider with a lineage, page by page, reason `deleted`
    (the datasets role's hourly `reconcile` is the backstop). 1-C3-1: with `connect`, the
    push also writes 0041's row (`PgSampleRestrictions`), not only the object record."""
    from ...datasets import lineage
    from ...state.lab_content import PgSampleRestrictions
    restrictions = None if connect is None else PgSampleRestrictions(connect)
    kw = {} if restrictions is None else {"restrictions": restrictions}

    async def push(stone) -> None:
        for provider in await lineage_providers(objects):
            while (await lineage.tombstone(
                    objects, provider_org_id=provider, grantor_org_id=stone.org_id,
                    request_id=stone.request_id, reason="deleted", at=stone.deleted_at,
                    **kw))["more"]:
                pass
    return push


def _traces(mode: str, env, objects=None, connect=None):
    """The role's `PilotSettings` (JUDGE_*, CLICKHOUSE_URL, S3_TRACE_BUCKET, the trace bounds)
    and T3's retention over them."""
    try:
        limits = validate_pilot(pilot_from_env(env))
    except ValueError as refused:
        raise RuntimeMisconfigured(mode, detail=str(refused)) from None
    try:
        return limits, trace_retention(limits, env.get("LAB_S3_ENDPOINT", ""), objects, connect)
    except Exception as failure:          # noqa: BLE001 - every failure refuses startup
        raise RuntimeMisconfigured(mode, detail="CLICKHOUSE_URL or S3_TRACE_BUCKET did not "
                                                f"answer ({type(failure).__name__})") from None


def database(connect):
    """`/readyz`'s dependency: the Lab database answers."""
    async def ready() -> bool:
        conn = await connect()
        try:
            await conn.execute("select 1")
        finally:
            await conn.close()
        return True
    return ready


# --- the roles ---------------------------------------------------------------------------
class DevTargets:
    """WR-COMP-2 (WR-B-3): a run's serving ref -> (its dev endpoint, its deployment
    revision), from L3's rows alone (0032 `PgControlStore.deployment`, 0007 through
    `PgCatalogDirectory`). The ref must be L3's own `serving_ref` (provider, revision id and
    the digest of the serving revision it serves now) of a PRIVATE DEV revision; anything
    else is not found
    (a run is never sent to another deployment). The call goes through the gateway that
    meters provider_dev, with the endpoint's credential, priced by the revision's active card
    (none yet: 503, the run waits)."""

    def __init__(self, deployments, catalog, *, url: str, key: str, client=None) -> None:
        self.deployments, self.catalog, self.url, self.key = deployments, catalog, url, key
        self.client = client or httpx.AsyncClient(timeout=300)   # one for every run

    async def __call__(self, ref: str):
        from ..control.operations import serving_ref
        match = lab.REF_RE.fullmatch(ref)
        found = await self.deployments.deployment(match.group(3)) \
            if match and match.group(1) == "serving" else None
        serving = found and await self.catalog.serving_revision(found.serving_version_id)
        if (not serving or found.environment is not Environment.dev
                or found.visibility is not Visibility.private
                or serving_ref(found, serving) != ref):
            raise errors.NotFound("no private dev revision of this provider serves this ref")
        card = await self.catalog.active_rate_card(found.deployment_revision_id)
        if card is None:
            raise errors.DependencyUnavailable("the dev revision has no active card yet")
        return HttpDevEndpoint(self.url.rstrip("/"), api_key=self.key,
                               model=serving.public_model_id, rate_card=card,
                               client=self.client), found


def _eval(mode, env, connect, objects, worker_id, **_):
    from ...state.catalog import PgCatalogDirectory
    from ...state.lab_control import PgControlStore
    from ...state.lab_data import PgLabDataStore
    store = PgLabDataStore(connect)
    targets = DevTargets(PgControlStore(connect), PgCatalogDirectory(connect),
                         url=env["LAB_EVAL_ENDPOINT_URL"], key=env["LAB_EVAL_ENDPOINT_KEY"])
    return worker_main.lab_eval(
        mode, connect, objects,
        lambda ref: store.evaluator(ref, provider_org_id=ref.split(":")[2]),   # WR-COMP-1
        targets, worker_id), None


class Checkpoints:
    """The Lab outbox's `checkpoint_received` handler: B3's decision, once per delivery
    (B3 makes a redelivery the same run). `CapacityExhausted` reaches the relay, which hands
    the event back for later; another kind is not this handler's (it stays pending)."""

    def __init__(self, store, ledger, access, registries, deployer) -> None:
        self.store, self.ledger, self.access = store, ledger, access
        self.registries, self.deployer = registries, deployer

    async def enqueue(self, event) -> bool:
        if event.kind != "checkpoint_received":
            raise errors.InvalidRequest(f"no checkpoints worker handles {event.kind}")
        await checkpoints.on_checkpoint(
            event.payload["checkpoint_id"], provider_org_id=event.provider_org_id,
            ledger=self.ledger, store=self.store, registries=self.registries,
            deployer=self.deployer, access=self.access)
        return True


def _checkpoints(mode, env, connect, objects, worker_id, registries=None, deployer=None):
    if not registries or deployer is None:
        raise RuntimeMisconfigured(mode, detail="checkpoints needs a registry adapter and "
                                   "L3's dev deployer (WR-B3-3): without them B3 would reject "
                                   "every checkpoint")
    from ...lab.access import LabAccess
    from ...state.lab_access import PgAccessStore
    from ...state.lab_data import PgLabDataStore
    from ...state.outbox import OutboxRelay
    ledger = lab_sql(mode, "lab_pipeline", "PgCheckpointLedger")(connect)
    store = PgLabDataStore(connect)
    # ponytail: one relay per role over one outbox: an event of the other role's kind is
    # refused and handed out again after the window (a `kinds` filter is WR-LSQ-C2B's).
    relay = OutboxRelay(store, Checkpoints(store, ledger, LabAccess(PgAccessStore(connect)),
                                           registries, deployer),
                        worker_id=f"{worker_id}-relay")
    return {"lab_checkpoints": lambda: every(worker_main.LAB_PUMP_S, relay.pump,
                                             "lab checkpoints")}, None


def _judge(mode, env, connect, objects, worker_id, **_):
    from ...judge.cost import APPROVED_RATES
    from ...judge.submit import HttpJudgeProvider, JudgeWiring
    from ...lab.access import LabAccess
    from ...state.lab_access import PgAccessStore
    from ...state.lab_consent import PgJudgeLedger
    try:
        provider = HttpJudgeProvider(env["JUDGE_PROVIDER_URL"])
    except errors.DomainError:
        raise RuntimeMisconfigured(mode, detail="JUDGE_PROVIDER_URL: judge egress is the "
                                                "local fake until P-10") from None
    limits, retention = _traces(mode, env)
    ledger = PgJudgeLedger(connect)
    wiring = JudgeWiring(access=LabAccess(PgAccessStore(connect)), ledger=ledger,
                         provider=provider, retention=retention, rates=APPROVED_RATES,
                         settings=limits)
    # ponytail: collect/reconcile need D6J's listing of submitted/ambiguous runs (lab-sql,
    # WR-LSQ-C2A); until then a run is collected by the door that submitted it.
    return {"judge_sweep": lambda: every(JUDGE_PASS_S, lambda: ledger.sweep(JUDGE_SILENT_S),
                                         "judge sweep")}, wiring


class JudgeReport:
    """WR-J3-D8-C: J3's report job on the judge role's `PgJudgeLedger` - `publish` once per
    configuration, each `{provider_org_id, org_id (the grantor), judge_model, rubric_version,
    results, feedback}`; one configuration's failure is counted and the next still runs.
    ponytail: called with its configurations; a timed pass needs lab-sql's listing of stored
    results and operator labels per configuration (WR-J3-D8-Cb)."""

    def __init__(self, ledger) -> None:
        self.ledger = ledger

    async def __call__(self, configurations) -> dict[str, int]:
        from ...judge.calibration import publish
        done = {"published": 0, "failed": 0}
        for c in configurations:
            try:
                await publish(self.ledger, c["results"], c["feedback"],
                              provider_org_id=c["provider_org_id"], org_id=c["org_id"],
                              judge_model=c["judge_model"], rubric_version=c["rubric_version"])
                done["published"] += 1
            except Exception:                 # noqa: BLE001 - the next configuration runs
                log.exception("judge report failed for one configuration")
                done["failed"] += 1
        return done


async def lineage_providers(objects) -> list[str]:
    """Every provider with a lineage under `lab/<provider>/lineage/`.
    ponytail: lists the Lab objects; a provider listing when that is too many keys."""
    return sorted({key.split("/")[1] for key in await objects.keys("lab/")
                   if key.split("/")[2:3] == ["lineage"]})


def _datasets(mode, env, connect, objects, worker_id, **_):
    from ...datasets import lineage
    from ...state.lab_access import PgAccessStore
    _, retention = _traces(mode, env, objects, connect)
    directory = PgAccessStore(connect)

    async def reconcile_all() -> dict[str, int]:
        report = {"providers": 0, "failed": 0}
        for provider in await lineage_providers(objects):
            report["providers"] += 1
            after = None
            try:
                while True:
                    after = (await lineage.reconcile(directory, retention, objects,
                                                     provider_org_id=provider,
                                                     after=after))["next"]
                    if after is None:
                        break
            except Exception:                 # noqa: BLE001 - the next provider still runs
                log.exception("lineage reconcile failed for one provider")
                report["failed"] += 1
        return report
    return {"lineage_reconcile": lambda: every(LINEAGE_PASS_S, reconcile_all,
                                               "lineage reconcile")}, None


def _rollout(mode, env, connect, objects, worker_id, **_):
    raise RuntimeMisconfigured(mode, detail="the rollout pass needs every running or "
                               "rolled-back D9 release with its frozen plan, R1's live "
                               "aggregates, the stored B2 report and L3's alias reads "
                               "(WR-LSQ-9): none is readable yet; emergency-rollback works")


def _annotation(mode, env, connect, objects, worker_id, **_):
    if env.get("LAB_ANNOTATION_TEACHER", "dry-run") != "dry-run":
        raise RuntimeMisconfigured(mode, detail="LAB_ANNOTATION_TEACHER: a teacher host needs "
                                   "its P-10 approval and N2's redaction (WR-P2-4)")
    raise RuntimeMisconfigured(mode, detail="annotation has no worker pass: a dry-run batch "
                               "is planned by the Lab route and nothing leaves; collecting "
                               "live batches needs their durable listing and N2's redaction "
                               "(WR-P2-4)")


def _training(mode, env, connect, objects, worker_id, **_):
    from ...pipelines.training import MANUAL
    if env.get("LAB_TRAINING_CONNECTOR", MANUAL) != MANUAL:
        raise RuntimeMisconfigured(mode, detail="LAB_TRAINING_CONNECTOR: an automatic "
                                   "connector is advertised only from a P-11 record (none)")
    raise RuntimeMisconfigured(mode, detail="training has no worker pass: the manual-bundle "
                               "run is prepared, submitted and finished through the Lab route")


def teacher_wiring(connect, objects, *, provider_url: str, settings, redact, rates=None):
    """WR-P2-D8-C: P2's `TeacherWiring` on the Lab database - D8's `PgTeacherLedger` (J2's
    ledger + `record_failures`, which `collect` calls; a plain `PgJudgeLedger` dies at the
    first per-item failure), P1's import over D8's label log, D7 and L2 on the same
    connection; J2's provider refuses any host but the local teacher fake (P-10). `redact` is
    N2's (WR-P2-4): the annotation role refuses until it exists, so no process builds this
    yet."""
    from ...judge.cost import APPROVED_RATES
    from ...judge.submit import HttpJudgeProvider
    from ...pipelines import annotations as p1
    from ...pipelines.teachers import TeacherWiring
    from ...state.lab_access import PgAccessStore
    from ...state.lab_data import PgLabDataStore
    from ...state.lab_pipeline import PgLabelLog, PgTeacherLedger
    return TeacherWiring(members=PgAccessStore(connect), ledger=PgTeacherLedger(connect),
                         provider=HttpJudgeProvider(provider_url),
                         store=PgLabDataStore(connect), objects=objects,
                         labels=p1.import_labels, log=PgLabelLog(connect),
                         rates=APPROVED_RATES if rates is None else rates, settings=settings,
                         redact=redact)


BUILD = {"eval": _eval, "checkpoints": _checkpoints, "judge": _judge,
         "annotation": _annotation, "training": _training, "rollout": _rollout,
         "datasets": _datasets}


def compose(role: str, env, *, objects=None, **sources) -> Worker:
    """The role's passes and readiness from its environment. `objects` is for tests only;
    `sources` are the checkpoints role's `registries`/`deployer` (WR-B3-3). Raises
    `RuntimeMisconfigured` naming what cannot serve; nothing connects but the bucket."""
    mode = f"lab-{role}"
    if role not in BUILD:
        raise RuntimeMisconfigured(mode, detail=f"the role must be one of {', '.join(ROLES)}")
    values = settings(mode, env, (DATABASE, PORT, *NEEDS[role]))
    connect = connector(values[DATABASE])
    if BUCKET in values and objects is None:
        objects = lab_objects(mode, env)
    worker_id = f"lab-{role}-{uuid.uuid4().hex[:8]}"
    tasks, wiring = BUILD[role](mode, {**env, **values}, connect, objects, worker_id,
                                **sources)
    worker = Worker(role=role, tasks=tasks, ready=database(connect), wiring=wiring)
    if role == "judge":                       # WR-J3-D8-C: the sweep's ledger
        worker.jobs["judge_report"] = JudgeReport(wiring.ledger)
    return worker


# --- the operator's stop (WR-I7-1) --------------------------------------------------------
async def emergency_rollback(env, policy_ref: str, reason: str) -> int:
    """R2's `emergency_rollback` once, as `LAB_OPERATOR_ID`, for the policy D7 holds for the
    provider the ref names. D9's decision lands first; the alias converges through L3 (its
    reads are WR-LSQ-9's: until then a non-zero exit after D9's rollback, rerun to converge)."""
    mode = "lab-rollout"
    values = settings(mode, env, (DATABASE, "LAB_OPERATOR_ID"))
    match = lab.REF_RE.fullmatch(policy_ref)
    if match is None or match.group(1) != "policy":
        raise RuntimeMisconfigured(mode, detail="--policy-ref must be a lab:policy ref")
    from ...gateway.pilot import control_serving
    from ...rollouts.control import Controller
    from ...state.lab_data import PgLabDataStore
    from ...state.lab_rollout import PgReleaseStore
    connect, operator = connector(values[DATABASE]), values["LAB_OPERATOR_ID"]
    try:
        policy = await PgLabDataStore(connect).resolve(policy_ref,
                                                       provider_org_id=match.group(2))
        controller = Controller(PgReleaseStore(connect), control_serving(connect, operator),
                                actor_id=operator)
        # ponytail: the wall clock stamps the decision record; D9's CAS orders it.
        await controller.emergency_rollback(operator, policy, policy_ref,
                                            now=datetime.now(timezone.utc), reason=reason)
    except errors.DomainError as failed:
        print(f"infrx.lab.workers: emergency rollback did not finish: {failed.code}: "
              f"{failed}", file=sys.stderr)
        return 1
    return 0


# --- the process ----------------------------------------------------------------------------
async def _answers(ready) -> bool:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            return bool(await ready())
    except Exception:                         # slow, refusing or broken: not ready
        return False


async def _probe(worker: Worker, tasks: dict, reader, writer) -> None:
    """A minimal HTTP/1.1 answer: `GET /livez`, `/readyz` or `/metrics`, then close."""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            request = (await reader.readline()).decode("latin-1").split()
            while (await reader.readline()).strip():
                pass
        method, path = (request + ["", ""])[:2]
        live = all(not task.done() for task in tasks.values())
        kind = "application/json"
        if method == "GET" and path == "/metrics":
            status, kind = 200, "text/plain; version=0.0.4"
            raw = ("# TYPE infrx_lab_worker_up gauge\n"
                   f'infrx_lab_worker_up{{role="{worker.role}"}} {int(live)}\n').encode()
        elif method == "GET" and path in ("/livez", "/readyz"):
            up = live and (path == "/livez" or await _answers(worker.ready))
            status, raw = (200 if up else 503), json.dumps({"role": worker.role, "live": live,
                                                            "up": up}).encode()
        else:
            status, raw = 404, b'{"error": "not_found"}'
        reason = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[status]
        writer.write(f"HTTP/1.1 {status} {reason}\r\ncontent-type: {kind}\r\n"
                     f"content-length: {len(raw)}\r\nconnection: close\r\n\r\n".encode() + raw)
        await writer.drain()
    except (TimeoutError, ConnectionError, ValueError):
        pass                                  # a broken probe gets no answer
    finally:
        writer.close()


async def serve(worker: Worker, port: int, *, stop: asyncio.Event | None = None) -> int:
    """Bind the loopback listener first (a busy port starts nothing), run every pass until
    SIGTERM/SIGINT (or `stop`), then cancel them: 0; a pass that ended on its own: 1."""
    stop = stop or asyncio.Event()
    running = asyncio.get_running_loop()
    tasks: dict[str, asyncio.Task] = {}
    server = await asyncio.start_server(partial(_probe, worker, tasks), "127.0.0.1", port)
    for sig in (signal.SIGTERM, signal.SIGINT):
        running.add_signal_handler(sig, stop.set)
    tasks.update({name: asyncio.create_task(factory(), name=name)
                  for name, factory in worker.tasks.items()})
    waiting = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({waiting, *tasks.values()}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        died = [task for task in tasks.values() if task.done()]
        for task in (waiting, *tasks.values()):
            task.cancel()
        await asyncio.gather(waiting, *tasks.values(), return_exceptions=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            running.remove_signal_handler(sig)
        server.close()
        await server.wait_closed()
    for task in died:
        log.error("pass %s ended; exiting for a restart", task.get_name(),
                  exc_info=None if task.cancelled() else task.exception())
    return 1 if died else 0


def main(argv=None, env=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="python -m infrx.lab.workers")
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("command", nargs="?", choices=("emergency-rollback",))
    parser.add_argument("--policy-ref")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    if args.command and not (args.role == "rollout" and args.policy_ref and args.reason):
        parser.error("emergency-rollback is the rollout role's, with --policy-ref and --reason")
    try:
        if args.command:
            return asyncio.run(emergency_rollback(env, args.policy_ref, args.reason))
        worker = compose(args.role, env)
    except ValueError as refused:             # RuntimeMisconfigured: names, never values
        print(f"infrx.lab.workers: refusing to start: {refused}", file=sys.stderr)
        return REFUSED
    return asyncio.run(serve(worker, int(env[PORT])))


if __name__ == "__main__":
    raise SystemExit(main())
