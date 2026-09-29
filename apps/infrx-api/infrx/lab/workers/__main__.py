"""E6L-O5 / WR-B-5 (amended) / WR-OBS-1 / WR-I6-3 / WR-I7-1: the Lab worker processes.

    python -m infrx.lab.workers <eval|checkpoints|judge|annotation|training|rollout|datasets>
    python -m infrx.lab.workers rollout emergency-rollback --policy-ref <ref> --reason <text>
    python -m infrx.lab.workers rollout decide --policy-ref <ref> --proposal-id <id>
        (--approve | --reject) --reason <text>
    python -m infrx.lab.workers rollout launch --policy-ref <ref> --plan <plan.json> --reason <text>

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
* `checkpoints` LAB_S3_BUCKET: B3's `on_checkpoint` per `checkpoint_received` event over D8's
               checkpoint ledger (0042), with the Lab registry of the event's own provider
               (`lab://<provider>/<path>`) and L3's dev deployer over 0044's reads (WR-B3-3).
* `judge`      JUDGE_PROVIDER_URL, CLICKHOUSE_URL, S3_TRACE_BUCKET (+ JUDGE_MODE, default
               dry_run, and JUDGE_LIVE_BUDGET_USD): J2's `JudgeWiring` over D6J's ledger (0036)
               and T3's retention; its passes move silent `submitting` runs to `ambiguous`
               and reconcile/collect the ambiguous/submitted runs of every provider with such
               work (WR-LSQ-C2A; 0053's listing, WR-C5-PROVIDERS);
               `jobs["judge_report"]` is J3's report on the same ledger (WR-J3-D8-C).
* `datasets`   LAB_S3_BUCKET, CLICKHOUSE_URL, S3_TRACE_BUCKET: N3's `lineage.reconcile` for
               every provider with a lineage, every page (WR-N3-2's pull half), and N1's
               imports from 0051's durable job queue (WR-N4-3).
* `rollout`    LAB_S3_BUCKET, LAB_OPERATOR_ID (the controller's audited principal): R2's
               `Controller.step` every 30 s for every running or rolled-back D9 release on
               the plan stored beside it (WR-R2-3; a running one only on R1's aggregates,
               held while unreadable). `emergency-rollback` (LAB_OPERATOR_ID of the invoking
               shell): R2's operator stop over D9 and L3. `decide`: the operator's decision
               of a Lab proposal through D9's CAS (WR-R4-2). `launch` (+ LAB_S3_BUCKET): the
               release launcher - the plan stored write-once, then D9's start (WR-C5-PLAN).
* `annotation` LAB_S3_BUCKET, LAB_TEACHER_URL (the local teacher fake until P-10; + JUDGE_MODE):
               P2's `TeacherWiring` with N2's redaction (WR-P2-4); its pass collects every
               submitted chunk run of every approved teacher batch (WR-P4B-2).
* `training`   no worker pass exists on this base (see `_training`); it refuses by name.
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
         "checkpoints": (BUCKET,), "judge": ("JUDGE_PROVIDER_URL", *TRACES),
         "annotation": (BUCKET, "LAB_TEACHER_URL"), "training": (BUCKET,),
         "rollout": (BUCKET, "LAB_OPERATOR_ID"),
         "datasets": (BUCKET, *TRACES)}
#: The Lab objects: the media bucket's store under `lab/<provider>/` (R182), at the media
#: store's prefix - `S3_MEDIA_PREFIX`'s default unless `LAB_S3_PREFIX` names the gateway's.
LAB_PREFIX = "infrx/"
# ponytail: fixed cadences, as the consumer worker's Lab pumps.
JUDGE_PASS_S = 60.0
JUDGE_SILENT_S = 300                  # a `submitting` run silent this long is `ambiguous`
JUDGE_BATCH = 100                     # runs per provider, state and pass (oldest first)
JUDGE_WORK = ("ambiguous", "submitted")   # the states the pass works (0053's provider listing)
LINEAGE_PASS_S = 3600.0               # the backstop behind WR-N3-2's push tombstones
IMPORT_PASS_S = 5.0                   # 0051's import-job queue, claimed
TEACHER_PASS_S = 60.0                 # a submitted teacher run's results, collected
ROLLOUT_PASS_S = 30.0                 # R2's controller pass over the live releases
ROLLOUT_STATES = ("running", "rolled_back")   # what the pass steps (R216: converge only)
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
    the event back for later; another kind is not this handler's. A checkpoint without B3's
    signed event is P3's (the Lab route decided it; its receipt wrote the same kind): done.
    `registries(provider)` is the registry adapters for that provider's events."""

    def __init__(self, store, ledger, access, registries, deployer) -> None:
        self.store, self.ledger, self.access = store, ledger, access
        self.registries, self.deployer = registries, deployer

    async def enqueue(self, event) -> bool:
        if event.kind != "checkpoint_received":
            raise errors.InvalidRequest(f"no checkpoints worker handles {event.kind}")
        checkpoint_id, provider = event.payload["checkpoint_id"], event.provider_org_id
        try:
            await self.ledger.event(checkpoint_id, provider_org_id=provider)
        except errors.NotFound:
            return True
        await checkpoints.on_checkpoint(
            checkpoint_id, provider_org_id=provider, ledger=self.ledger, store=self.store,
            registries=self.registries(provider), deployer=self.deployer, access=self.access)
        return True


def _checkpoints(mode, env, connect, objects, worker_id, registries=None, deployer=None):
    """WR-B3-3: the Lab registry over the role's objects and L3's dev deployer over
    `PgControlStore` (0044's reads) unless a test passes its own."""
    from ...lab.access import LabAccess
    from ...state.lab_access import PgAccessStore
    from ...state.lab_control import PgControlStore
    from ...state.lab_data import PgLabDataStore
    from ...state.outbox import OutboxRelay
    ledger = lab_sql(mode, "lab_pipeline", "PgCheckpointLedger")(connect)
    store = PgLabDataStore(connect)
    per_provider = (lambda _: registries) if registries else \
        partial(checkpoints.lab_registry, objects)
    deployer = deployer or checkpoints.DevDeployer(PgControlStore(connect))
    # R215 / WR-LSQ-C2B: this role's relay claims its own kind only
    relay = OutboxRelay(worker_main.Kinds(store, ("checkpoint_received",)),
                        Checkpoints(store, ledger, LabAccess(PgAccessStore(connect)),
                                    per_provider, deployer),
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
    return {"judge_sweep": lambda: every(JUDGE_PASS_S, lambda: ledger.sweep(JUDGE_SILENT_S),
                                         "judge sweep"),
            "judge_collect": lambda: every(JUDGE_PASS_S, lambda: judge_pass(
                wiring, partial(ledger.providers_in, JUDGE_WORK)), "judge collect")}, wiring


async def judge_pass(wiring, providers) -> dict[str, int]:
    """WR-LSQ-C2A: for every provider, D6J's `ambiguous` runs (`runs_in`, 0049) are looked up
    by their submit key - the provider's record moves the run to `submitted`; none leaves it
    as it is (R184/R192: never resubmitted or released by the platform) - then every
    `submitted` run is J2's `collect`. A teacher run (consented by its dataset ref) is the
    annotation role's. One run's failure is counted; the next still runs."""
    from ...judge import submit
    done = {"reconciled": 0, "waiting": 0, "collected": 0, "failed": 0}
    ledger = wiring.ledger
    for provider in await providers():
        for state in JUDGE_WORK:
            for run in await ledger.runs_in((state,), JUDGE_BATCH, provider_org_id=provider):
                if run.consent.grant_id.startswith("lab:"):
                    continue
                try:
                    if state == "submitted":
                        await submit.collect(run.run_id, wiring=wiring)
                        done["collected"] += 1
                    elif (external := await wiring.provider.lookup(run.submit_key)) is None:
                        done["waiting"] += 1
                    else:
                        await ledger.record_submission(run.run_id, external)
                        done["reconciled"] += 1
                except Exception:             # noqa: BLE001 - the next run still runs
                    log.exception("judge pass failed for one run")
                    done["failed"] += 1
    return done


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
    from ...state.lab_content import PgSampleRestrictions
    _, retention = _traces(mode, env, objects, connect)
    # WR-DS5-2: 0041 written through its own port on this login, not found via the directory
    directory, restrictions = PgAccessStore(connect), PgSampleRestrictions(connect)

    async def reconcile_all() -> dict[str, int]:
        report = {"providers": 0, "failed": 0}
        for provider in await lineage_providers(objects):
            report["providers"] += 1
            after = None
            try:
                while True:
                    after = (await lineage.reconcile(directory, retention, objects,
                                                     provider_org_id=provider, after=after,
                                                     restrictions=restrictions))["next"]
                    if after is None:
                        break
            except Exception:                 # noqa: BLE001 - the next provider still runs
                log.exception("lineage reconcile failed for one provider")
                report["failed"] += 1
        return report
    from ...datasets import imports
    from ...state.lab_data import PgLabDataStore, PgLabImportJobs
    jobs, store = PgLabImportJobs(connect), PgLabDataStore(connect)
    return {"lineage_reconcile": lambda: every(LINEAGE_PASS_S, reconcile_all,
                                               "lineage reconcile"),
            "import_jobs": lambda: every(IMPORT_PASS_S, lambda: imports.work(
                jobs, store, objects, worker_id=worker_id), "import jobs")}, None


class NoLive:
    """R1's `Live` aggregates for a running release. R1 records assignments only: no error,
    latency, spend or health aggregate per arm is readable (WR-C5-LIVE), so a running
    release is held - never evaluated on invented numbers."""

    async def __call__(self, listing):
        raise errors.DependencyUnavailable("R1's live aggregates are not readable (WR-C5-LIVE)")


def plan_key(provider_org_id: str, policy_id: str) -> str:
    """R2's full `Plan` of a release, stored write-once beside it by its launcher
    (`rollout launch`, WR-C5-PLAN; D9 keeps only its digest, which `Controller` checks)."""
    return f"lab/{provider_org_id}/releases/{policy_id}/plan.json"


async def rollout_pass(objects, store, releases, controller, live) -> dict[str, int]:
    """WR-R2-3: `Controller.step` for every running or rolled-back D9 release of every
    provider D9 lists with one (0053, WR-C5-PROVIDERS), on its stored plan and D7's policy;
    one without a stored plan is counted held (0-F2: the report shows the gap). A running
    release needs R1's aggregates (held while unreadable); a rolled-back one only
    converges (R216). ponytail: no stored B2 report is linked to a release yet
    (WR-C5-REPORT), so none is passed and a running release holds `no_report`; the wall
    clock is `now`, D9's CAS orders the decisions."""
    from ...rollouts.control import Plan
    done = {"stepped": 0, "held": 0, "failed": 0}
    for provider in await releases.providers_in(ROLLOUT_STATES):
        for item in await releases.releases_in(ROLLOUT_STATES, provider_org_id=provider):
            try:
                raw = await objects.get(plan_key(provider, item.policy_id))
                if raw is None:
                    done["held"] += 1
                    continue
                current = await live(item) if item.release.state == "running" else None
                policy = await store.resolve(item.policy_ref, provider_org_id=provider)
                await controller.step(policy, item.policy_ref, Plan.model_validate_json(raw),
                                      current, now=datetime.now(timezone.utc))
                done["stepped"] += 1
            except errors.DependencyUnavailable:
                done["held"] += 1
            except Exception:                 # noqa: BLE001 - the next release still runs
                log.exception("rollout pass failed for one release")
                done["failed"] += 1
    return done


def _rollout(mode, env, connect, objects, worker_id, live=None, **_):
    from ...gateway.pilot import control_serving
    from ...rollouts.control import Controller
    from ...state.lab_data import PgLabDataStore
    from ...state.lab_rollout import PgReleaseStore
    operator, releases = env["LAB_OPERATOR_ID"], PgReleaseStore(connect)
    controller = Controller(releases, control_serving(connect, operator), actor_id=operator)
    store, live = PgLabDataStore(connect), live or NoLive()
    return {"rollout_pass": lambda: every(ROLLOUT_PASS_S, lambda: rollout_pass(
        objects, store, releases, controller, live), "rollout pass")}, controller


def _annotation(mode, env, connect, objects, worker_id, **_):
    if env.get("LAB_ANNOTATION_TEACHER", "dry-run") != "dry-run":
        raise RuntimeMisconfigured(mode, detail="LAB_ANNOTATION_TEACHER: a teacher host needs "
                                   "its P-10 approval")
    from ...datasets.versions import redact_content
    try:
        pilot = validate_pilot(pilot_from_env(env))
    except ValueError as refused:
        raise RuntimeMisconfigured(mode, detail=str(refused)) from None
    try:
        wiring = teacher_wiring(connect, objects, provider_url=env["LAB_TEACHER_URL"],
                                settings=pilot, redact=redact_content)
    except errors.DomainError:            # names the setting, never its value
        raise RuntimeMisconfigured(mode, detail="LAB_TEACHER_URL: teacher egress is the local "
                                                "teacher fake until P-10") from None
    return {"teacher_collect": lambda: every(TEACHER_PASS_S, lambda: collect_teachers(wiring),
                                             "teacher collect")}, wiring


async def collect_teachers(wiring) -> dict[str, int]:
    """WR-P4B-2: P2's `collect` for every `submitted` chunk run (D8's `PgTeacherLedger`) of
    every APPROVED teacher batch - the Lab route's write-once `batch.json` with its
    `approval.json` (R202) under `lab/<provider>/teacher-batches/<id>/` - as the approver;
    `collect` imports the labels once, records the per-item failures and settles once.
    ponytail: lists the Lab objects and replans each batch per pass; D8's listing of
    submitted runs (WR-LSQ-C2A) when that is too many keys."""
    from ...gateway.routes.lab_pipelines import _teacher
    from ...pipelines import teachers as p2
    done, objects = {"collected": 0, "failed": 0}, wiring.objects
    for key in sorted(await objects.keys("lab/")):
        parts = key.split("/")
        if parts[2:3] != ["teacher-batches"] or parts[-1] != "approval.json":
            continue
        try:
            stored = json.loads(await objects.get(key.removesuffix("approval.json")
                                                  + "batch.json"))
            batch = _teacher(stored, parts[1], json.loads(await objects.get(key))["approved_by"])
            planned = await p2.plan(batch, store=wiring.store, objects=objects,
                                    rates=wiring.rates, now=await wiring.ledger.db_now())
            runs = [run_id for run_id, _ in planned.chunks
                    if getattr(await wiring.ledger.run(run_id), "state", None) == "submitted"]
        except Exception:                     # noqa: BLE001 - the next batch still runs
            log.exception("teacher batch unreadable")
            done["failed"] += 1
            continue
        for run_id in runs:
            try:
                await p2.collect(batch, run_id, wiring=wiring)
                done["collected"] += 1
            except Exception:                 # noqa: BLE001 - the next run still runs
                log.exception("teacher collect failed for one run")
                done["failed"] += 1
    return done


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
    N2's `versions.redact_content` (WR-P2-4) in the annotation role and the gateway."""
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
    `sources` replace the checkpoints role's `registries`/`deployer` and the rollout role's
    `live` in tests. Raises
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


async def launch_release(env, policy_ref: str, plan_path: str, reason: str) -> int:
    """WR-C5-PLAN: the release launcher, as `LAB_OPERATOR_ID`. R2's `Plan` (read from
    `plan_path`; anything else refuses before a write) is stored write-once beside the
    release (`plan_key`), THEN D9 starts the revision `policy_ref` names with that plan's
    digest: no pass or page sees a started release without its plan. A replay stores the same
    bytes; another plan for a stored one is a conflict and D9 is not asked."""
    mode = "lab-rollout"
    values = settings(mode, env, (DATABASE, BUCKET, "LAB_OPERATOR_ID"))
    match = lab.REF_RE.fullmatch(policy_ref)
    if match is None or match.group(1) != "policy":
        raise RuntimeMisconfigured(mode, detail="--policy-ref must be a lab:policy ref")
    from ...datasets.imports import write_once
    from ...rollouts.control import Plan, plan_digest
    from ...state.lab_data import PgLabDataStore
    from ...state.lab_rollout import PgReleaseStore
    try:
        with open(plan_path, "rb") as stored:
            plan = Plan.model_validate_json(stored.read())
    except (OSError, ValueError) as unreadable:
        raise RuntimeMisconfigured(mode, detail=f"--plan is not R2's plan "
                                                f"({type(unreadable).__name__})") from None
    connect, provider = connector(values[DATABASE]), match.group(2)
    try:
        policy = await PgLabDataStore(connect).resolve(policy_ref, provider_org_id=provider)
        await write_once(lab_objects(mode, env), plan_key(provider, policy.policy_id),
                         plan.model_dump_json().encode())
        await PgReleaseStore(connect).start(policy_ref, provider_org_id=provider,
                                            plan_digest=plan_digest(plan),
                                            decided_by=values["LAB_OPERATOR_ID"], reason=reason)
    except errors.DomainError as failed:
        print(f"infrx.lab.workers: the release was not launched: {failed.code}: {failed}",
              file=sys.stderr)
        return 1
    return 0


async def decide_proposal(env, policy_ref: str, proposal_id: str, approve: bool,
                          reason: str) -> int:
    """WR-R4-2: the operator decides one of the Lab's proposals for the release `policy_ref`
    names, as `LAB_OPERATOR_ID`, through 0043's decision: D9's CAS at the proposal's fence and
    the proposal's state in one transaction (a stale fence refuses and leaves it pending). A
    rejection moves nothing. An approved rollback is R2's `lab.rollout_decision.1` by the
    operator; R2's operator stop then converges the alias (its CAS finds the release rolled
    back). An approved expansion needs R2's `expand` verdict on R1's aggregates, which are
    not readable (WR-C5-LIVE): refused, nothing decided."""
    mode, needs = "lab-rollout", (DATABASE, "LAB_OPERATOR_ID")
    values = settings(mode, env, needs)
    match = lab.REF_RE.fullmatch(policy_ref)
    if match is None or match.group(1) != "policy":
        raise RuntimeMisconfigured(mode, detail="--policy-ref must be a lab:policy ref")
    from ...gateway.pilot import control_serving
    from ...rollouts.control import Controller
    from ...state.lab_data import PgLabDataStore
    from ...state.lab_rollout import PgReleaseProposals, PgReleaseStore
    connect, operator, provider = connector(values[DATABASE]), values["LAB_OPERATOR_ID"], \
        match.group(2)
    proposals = PgReleaseProposals(connect)
    try:
        found = next((p for p in await proposals.proposals(provider_org_id=provider)
                      if str(p["proposal_id"]) == proposal_id and p["policy_ref"] == policy_ref),
                     None)
        if found is None:
            raise errors.NotFound("no such proposal for this release")
        if not approve:
            await proposals.decide(proposal_id, approve=False, decided_by=operator)
            return 0
        if found["kind"] != "rollback":
            raise errors.DependencyUnavailable("an expansion is approved on R2's expand verdict "
                                               "over R1's aggregates (WR-C5-LIVE)")
        now = datetime.now(timezone.utc)
        await proposals.decide(proposal_id, approve=True, decided_by=operator, decision={
            "schema": "lab.rollout_decision.1", "provider_org_id": provider,
            "policy_ref": policy_ref, "decision": "rollback", "evidence_refs": [],
            "decided_by": operator, "decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
            reasons=(f"operator:{reason}", f"proposal:{proposal_id}"))
        policy = await PgLabDataStore(connect).resolve(policy_ref, provider_org_id=provider)
        serving = control_serving(connect, operator)
        controller = Controller(PgReleaseStore(connect), serving, actor_id=operator)
        await controller.emergency_rollback(operator, policy, policy_ref, now=now, reason=reason)
    except errors.DomainError as failed:
        print(f"infrx.lab.workers: the proposal was not decided: {failed.code}: {failed}",
              file=sys.stderr)
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
    parser.add_argument("command", nargs="?", choices=("emergency-rollback", "decide", "launch"))
    parser.add_argument("--plan")
    parser.add_argument("--policy-ref")
    parser.add_argument("--reason")
    parser.add_argument("--proposal-id")
    verdict = parser.add_mutually_exclusive_group()
    verdict.add_argument("--approve", action="store_true")
    verdict.add_argument("--reject", action="store_true")
    args = parser.parse_args(argv)
    if args.command and not (args.role == "rollout" and args.policy_ref and args.reason):
        parser.error("emergency-rollback is the rollout role's, with --policy-ref and --reason")
    if args.command == "decide" and not (args.proposal_id and (args.approve or args.reject)):
        parser.error("decide names --proposal-id and --approve or --reject")
    if args.command == "launch" and not args.plan:
        parser.error("launch names --plan (R2's plan as JSON)")
    try:
        if args.command == "launch":
            return asyncio.run(launch_release(env, args.policy_ref, args.plan, args.reason))
        if args.command == "decide":
            return asyncio.run(decide_proposal(env, args.policy_ref, args.proposal_id,
                                               args.approve, args.reason))
        if args.command:
            return asyncio.run(emergency_rollback(env, args.policy_ref, args.reason))
        worker = compose(args.role, env)
    except ValueError as refused:             # RuntimeMisconfigured: names, never values
        print(f"infrx.lab.workers: refusing to start: {refused}", file=sys.stderr)
        return REFUSED
    return asyncio.run(serve(worker, int(env[PORT])))


if __name__ == "__main__":
    raise SystemExit(main())
