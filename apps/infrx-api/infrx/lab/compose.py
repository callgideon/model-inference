"""A1: the Lab compositions - every Lab surface, store and helper the gateway, the Lab control
service and the Lab workers build, in one module none of them imports back (it replaces the
Lab half of `gateway.pilot` and the CLI helpers the gateway imported from
`lab/workers/__main__.py`; `pilot` re-exports the old names). Each is OFF unless its switch is
on; nothing here runs at import.

* `lab_surfaces` (was `pilot._lab`): `LAB_CONTROL`/`LAB_TRACES`/`LAB_DATASETS` and, through
  `lab_evaluations_pipelines_releases` (was `_lab_2`), `LAB_EVALS`/`LAB_PIPELINES`/
  `LAB_RELEASES` - callers `pilot.adapters_from_env`, `lab.control.app._families`.
* `lab_teachers` (was `_teachers`, `LAB_TEACHERS` beside `LAB_PIPELINES`) over
  `teacher_wiring` - also the annotation role's (`lab.workers`).
* `lab_checkpoints` (`LAB_CHECKPOINTS`) - `pilot.adapters_from_env`, `lab.control.app`.
* `lab_traces` (`LAB_TRACES`) - `lab_surfaces`, `lab.control.app._compose`.
* `lab_control` / `lab_operations` - `lab_surfaces`, `lab.control.app._compose`;
  `control_serving` - the rollout role and `rollout decide|emergency-rollback` (`lab.workers`).
* `lab_releases` with its read models (`ReleaseRecords`, `ReleaseProposals`, `SHOWN`,
  `_progress`, `ReportUnavailable`) and the release helpers the rollout role shares
  (`plan_key`, `release_live`, `release_report`).
* `lab_optimizations` (R3's store): R266's composition, awaiting its CLI caller (A11,
  WR-R3I-OPEN in the carried-work register) - only tests call it today; kept, not dead.
"""
from __future__ import annotations

import hashlib
import json

from ..config import RuntimeMisconfigured, runtime_mode
from ..contracts import errors
from ..contracts.lab import records as lab
from ..state.catalog import PgCatalogDirectory
from .time import iso_z as _z  # noqa: F401 - A6 (WR-L4-1): the release read models' `...Z`


async def release_live(releases, listing):
    """R244 (WR-C6-LIVE): R2's `Live` of a running release, read by D9 per arm from R1's
    assignments and the admitted jobs they name (0054). Nothing assigned yet is no
    observation: held, never evaluated on zeros."""
    current = await releases.live(listing.policy_ref)
    if current is None:
        raise errors.DependencyUnavailable("no admitted request is assigned to this release yet")
    return current


def plan_key(provider_org_id: str, policy_id: str) -> str:
    """R2's full `Plan` of a release, stored write-once beside it by its launcher
    (`rollout launch`, WR-C5-PLAN; D9 keeps only its digest, which `Controller` checks)."""
    return f"lab/{provider_org_id}/releases/{policy_id}/plan.json"


async def release_report(reads, store, provider: str, policy, plan):
    """WR-C5-REPORT: the release's B2 report and its two runs (D7's records) - the provider's
    NEWEST experiment (B4's launch record, 0043's listing) with a stored report under the
    plan's own protocol whose runs are the policy's baseline and one of its candidates - or
    `(None, None)` (R2 then holds `no_report`). R2 checks the binding again."""
    protocol = "sha256:" + hashlib.sha256(lab.canonical(plan.protocol)).hexdigest()
    for e in await reads.experiments(provider_org_id=provider):          # newest first
        if e["report"] is None or e["protocol_digest"] != protocol:
            continue
        runs = tuple([(await store.resolve(e[arm]["run_ref"], provider_org_id=provider))
                      .model_dump(mode="json", by_alias=True, exclude_unset=True)   # its ref
                      for arm in ("baseline", "candidate")])
        if runs[0]["serving_ref"] == policy.baseline_ref and \
                runs[1]["serving_ref"] in {c.serving_ref for c in policy.candidates}:
            return ({**json.loads(e["report"]["body"]),
                     "report_digest": e["report"]["report_digest"]}, runs)
    return None, None



def teacher_wiring(connect, objects, *, provider_url: str, settings, redact, rates=None):
    """WR-P2-D8-C: P2's `TeacherWiring` on the Lab database - D8's `PgTeacherLedger` (J2's
    ledger + `record_failures`, which `collect` calls; a plain `PgJudgeLedger` dies at the
    first per-item failure), P1's import over D8's label log, D7 and L2 on the same
    connection; J2's provider refuses any host but the local teacher fake (P-10). `redact` is
    N2's `versions.redact_content` (WR-P2-4) in the annotation role and the gateway."""
    from ..judge.cost import APPROVED_RATES
    from ..judge.submit import HttpJudgeProvider
    from ..pipelines import annotations as p1
    from ..pipelines.teachers import TeacherWiring
    from ..state.lab_access import PgAccessStore
    from ..state.lab_data import PgLabDataStore
    from ..state.lab_pipeline import PgLabelLog, PgTeacherLedger
    return TeacherWiring(members=PgAccessStore(connect), ledger=PgTeacherLedger(connect),
                         provider=HttpJudgeProvider(provider_url),
                         store=PgLabDataStore(connect), objects=objects,
                         labels=p1.import_labels, log=PgLabelLog(connect),
                         rates=APPROVED_RATES if rates is None else rates, settings=settings,
                         redact=redact)



def lab_surfaces(settings, connect, objects=None) -> dict:
    """LAB-API: `lab_control` / `lab_traces` for the switches that are on, over one session
    verifier (the project's auth server) and L2's `LabAccess` on this pool. L3's control
    operations are not composed until L3 merges (its routes answer 503; health is served).
    `LAB_TRACES` needs T2I's projection and trace bucket, or startup is refused."""
    deployment = settings.deployment
    teachers = lab_teachers(settings, connect, objects)
    if not (deployment.lab_control or deployment.lab_traces or deployment.lab_evals
            or deployment.lab_pipelines or deployment.lab_releases or deployment.lab_datasets):
        return {}
    import httpx

    from .access import LabAccess
    from ..state.lab_access import PgAccessStore
    from ..gateway.lab_auth import GoTrueSessions
    from ..gateway.routes.lab_control import LabControl
    # ponytail: this client lives as long as the process; close it in `lifespan` if an app is
    # ever rebuilt inside one process outside tests.
    sessions = GoTrueSessions(httpx.AsyncClient(base_url=settings.supabase_url,
                                                timeout=httpx.Timeout(5, connect=2)),
                              settings.supabase_key)
    access = LabAccess(PgAccessStore(connect))
    lab = {"lab_control": LabControl(sessions, access, lab_operations(connect, access))} \
        if deployment.lab_control else {}
    if deployment.lab_traces:
        lab["lab_traces"] = lab_traces(settings, connect, sessions, access)
    if deployment.lab_datasets:           # WR-N4-1 over D7, L2 and the Lab objects (R182)
        from ..state.lab_data import PgLabDataStore, PgLabImportJobs
        from ..gateway.routes.lab_datasets import LabDatasets
        lab["lab_datasets"] = LabDatasets(sessions, access, PgLabDataStore(connect), objects,
                                          PgLabImportJobs(connect))    # WR-C5-N4-ROUTE (0051)
    return {**lab, **lab_evaluations_pipelines_releases(deployment, connect, sessions, access,
                                                        objects, teachers)}


def lab_teachers(settings, connect, objects):
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
    try:
        return teacher_wiring(connect, objects, provider_url=deployment.lab_teacher_url,
                              settings=settings.pilot, redact=redact_content)
    except errors.DomainError:            # names the setting, never its value
        raise RuntimeMisconfigured(mode, detail="LAB_TEACHER_URL: teacher egress is the local "
                                                "teacher fake until P-10") from None


def lab_checkpoints(settings, connect) -> dict:
    """WR-B3-2: B3's receiver over D8's checkpoint ledger (0042) and D7, with the key
    directory `LAB_CHECKPOINT_KEYS` names; on without a valid directory refuses to start."""
    deployment = settings.deployment
    if not deployment.lab_checkpoints:
        return {}
    from ..state.lab_data import PgLabDataStore
    from ..gateway.routes.lab_checkpoints import LabCheckpoints, key_directory
    mode = runtime_mode(settings)
    try:
        keys = key_directory(deployment.lab_checkpoint_keys)
    except ValueError as refused:         # names the setting, never its value
        raise RuntimeMisconfigured(mode, ("LAB_CHECKPOINT_KEYS",), detail=str(refused)) \
            from None
    from ..state.lab_pipeline import PgCheckpointLedger
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


def lab_evaluations_pipelines_releases(deployment, connect, sessions, access, objects=None,
                                       teachers=None) -> dict:
    """LAB-API-2: the evaluation, pipeline and release surfaces for the switches that are on,
    over D7 (`PgLabDataStore`, merged); the pipelines over D8's label log and run ledger
    (WR-P1-D8-C / WR-P3-D8-C), the Lab objects and P3's evaluation port over B3/B1
    (WR-E7L-1) with the production suites (WR-C4-B3-SUITES: D7's receipt, D8's subscription,
    L3's dev deployer on this pool). The evaluation surface's experiments, ledger and
    catalog ports are AP-10's (`lab.evaluation.evaluation_ports`, WR-AP10-1) over 0043/0034/
    D8; the catalog listing answers 503 until SR-AP10-1. The pipeline run listings are still
    absent (503); the release surface is WR-R4-2's (`lab_releases`)."""
    from ..evaluation import checkpoints
    from ..state.lab_data import PgLabDataStore
    from ..state.lab_pipeline import PgLabelLog, PgRunLedger
    from ..gateway.routes.lab_evaluations import LabEvaluations
    from ..gateway.routes.lab_pipelines import LabPipelines
    from .evaluation import evaluation_ports
    store = PgLabDataStore(connect)
    return {**({"lab_evaluations": LabEvaluations(sessions, access, store=store,
                                                  **evaluation_ports(connect))}
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
    from ..state.lab_data import PgLabDataStore, PgLabReads
    from ..state.lab_rollout import PgReleaseProposals, PgReleaseStore
    from ..state.lab_variants import PgLabVariants
    from ..gateway.routes.lab_releases import LabReleases
    d9 = PgReleaseStore(connect)
    return LabReleases(sessions, access,
                       records=ReleaseRecords(d9, PgLabDataStore(connect), objects,
                                              PgLabVariants(connect), PgLabReads(connect)),
                       proposals=ReleaseProposals(PgReleaseProposals(connect)), store=d9)


# --- WR-LW9-4 (R263): R3's store on this pool ---------------------------------------------
def lab_optimizations(connect):
    """R3's `optimization.store` over D7 and 0058's identities port on this pool; the caller
    names `identities=(base, variant)`, the pair `register` derived both serving refs from.
    R266's production composition; A11: no production caller yet - a Lab workers CLI command
    (`optimization register`) will compose through it (carried-work register, WR-R3I-OPEN)."""
    import functools

    from ..rollouts import optimization
    from ..state.lab_data import PgLabDataStore
    from ..state.lab_variants import PgLabVariants
    return functools.partial(optimization.store, PgLabDataStore(connect),
                             variants=PgLabVariants(connect))


SHOWN = ("running", "approved", "rolled_back")      # the Lab's release states (port.ts)


def _progress(live, assignments) -> dict | None:
    """R2's `Live` in port.ts's `Progress` shape (snake_case) with D9's per-serving tally
    (0058, WR-C7-TALLY), or None: nothing observed."""
    if live is None:
        return None

    def arm(a) -> dict:
        return {"requests": a.requests, "errors": a.errors, "p99_ms": a.p99_ms}
    return {"observed_until": _z(live.observed_until), "baseline": arm(live.baseline),
            "candidate": arm(live.candidate), "quality_covered": live.quality_covered,
            "spent": {"amount": live.spent.value, "unit": live.spent.unit},
            "candidate_healthy": live.candidate_healthy, "assignments": assignments}


class ReportUnavailable(Exception):
    """R260 (1-LR7-RV-3): one B4 experiment row the B2 report cannot be read from (its runs,
    its body): that release's verdict only, never the listing."""


class ReleaseRecords:
    """WR-R4-2: `/lab/v1/releases`' read models (port.ts, snake_case). Each D9 release (0048)
    with D7's policy revision and the plan its launcher stored (WR-C5-PLAN; none stored: a
    503 naming it, never a guessed plan); `progress` is D9's Live of the revision (0054, R244;
    WR-LIVE-PAGE), null only while nothing is assigned, its `assignments` D9's per-(serving,
    pin) tally of terminal requests (0058, WR-C7-TALLY), read only once Live observed something;
    the verdict is `verdict`'s (WR-LR6-VERDICT, R259). A Live R248
    refuses (legacy USD) nulls that row's progress and verdict with `refused: "unit_refused"`
    (C7-RV-6, R255); a B2 report that cannot be read nulls that row's verdict with
    `refused: "report_unavailable"` (R260); the rest list. Decisions are 0053's. R3's variants
    are 0055's listing (WR-C6-VARIANTS): none is [], never a 503."""

    def __init__(self, d9, store, objects, variants, reads=None) -> None:
        self.d9, self.store, self.objects, self.lab_variants = d9, store, objects, variants
        self.reads = reads                          # B4's experiments (0043): B2's report

    async def verdict(self, provider_org_id: str, item, policy, plan, live) -> dict | None:
        """WR-LR6-VERDICT: D9's latest decision; else, for a running release, R2's `evaluate`
        at read time over the Live its progress shows (0054, at the database clock of that
        read) and its B2 report (`release_report`, WR-C5-REPORT) - read-only, never recorded.
        Null while nothing is assigned (R244) or R2 refuses the plan's unit (R248). Unwired
        B4 (`reads=None`) is a typed 503 (1-LR7-RV-2); an unreadable B4 row is
        `ReportUnavailable` (R260); an outage still fails the listing."""
        from ..rollouts.control import evaluate
        d = item.latest_decision
        if d is not None:
            return {"action": d.decision, "reasons": list(d.reasons),
                    "evidence_refs": list(d.evidence_refs), "evaluated_at": _z(d.at)}
        if item.release.state != "running" or live is None:
            return None
        if self.reads is None:                      # 1-LR7-RV-2: never an AttributeError
            raise errors.DependencyUnavailable("B4's experiments are not wired (WR-LR6-VERDICT)")
        try:
            report, runs = await release_report(self.reads, self.store, provider_org_id,
                                                policy, plan)
        except (errors.NotFound, KeyError, TypeError, ValueError) as bad:   # R260: a bad row
            raise ReportUnavailable(str(bad)) from bad
        try:
            v = evaluate(plan, policy, live, started_at=item.release.started_at,
                         now=live.observed_until, report=report, runs=runs)
        except errors.InvalidRequest:
            return None
        return {"action": v.action, "reasons": list(v.reasons),
                "evidence_refs": list(v.evidence_refs), "evaluated_at": _z(live.observed_until)}

    async def releases(self, provider_org_id: str) -> list[dict]:
        from ..rollouts.control import Plan
        out = []
        for item in await self.d9.releases_in(SHOWN, provider_org_id=provider_org_id):
            raw = await self.objects.get(plan_key(provider_org_id, item.policy_id))
            if raw is None:
                raise errors.DependencyUnavailable(
                    f"the plan of {item.policy_ref} is not stored (WR-C5-PLAN)")
            full = Plan.model_validate_json(raw)
            plan = full.model_dump(mode="json")
            policy = await self.store.resolve(item.policy_ref, provider_org_id=provider_org_id)
            release = item.release
            try:              # one Live read: the progress shown and the verdict judged on it
                live, refused = await self.d9.live(item.policy_ref), None
            except errors.InvalidRequest:     # R248, C7-RV-6: this row only, typed
                live, refused = None, "unit_refused"
            try:
                verdict = await self.verdict(provider_org_id, item, policy, full, live)
            except ReportUnavailable:         # R260, 1-LR7-RV-3: this row only, typed
                verdict, refused = None, "report_unavailable"
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
                "started_at": _z(release.started_at),
                "progress": _progress(live, None if live is None else
                                      await self.d9.tally(item.policy_ref)),
                "verdict": verdict})
            if refused:
                out[-1].update(verdict=None, refused=refused)
        return out

    async def decisions(self, provider_org_id: str) -> list[dict]:
        return [{**d, "decided_at": _z(d["decided_at"])}
                for d in await self.d9.decisions(provider_org_id=provider_org_id)]

    async def variants(self, provider_org_id: str) -> list[dict]:
        return await self.lab_variants.variants(provider_org_id)


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
    from .control import LabControl
    from .control.app import NoEngine
    from ..state.lab_control import PgControlStore
    from ..state.operations import PgRegistry
    return LabControl(access, PgControlStore(connect), PgRegistry(connect),
                      PgCatalogDirectory(connect), engine=NoEngine())


def lab_operations(connect, access):
    """WR-LAB-API-2 / WR-LSQ-9-C: L3's `Operations` for `/lab/v1/control` - the gateway's and
    the I2L control service's one composition (`infrx.lab.control.app`, WR-LAB-API-2c). Its
    listings and registration read `PgControlStore` (0044's `infrx_lab_control` reads:
    provider_servings, provider_deployments, endpoint_alias, listing_versions)."""
    from .control.operations import Operations
    from ..state.lab_control import PgControlStore
    return Operations(lab_control(connect, access), PgControlStore(connect))


def control_serving(connect, principal: str):
    """WR-R2-2's composition: R2's `ServingControl` as L3's `Serving`, acting as
    `principal` (the audited actor of every alias CAS). WR-LSQ-9-C: reads are the real
    `PgControlStore`, not a typed-503 stand-in."""
    from .access import LabAccess
    from .control.operations import Serving
    from ..operations.service import OperatorSession
    from ..state.lab_access import PgAccessStore
    from ..state.lab_control import PgControlStore
    return Serving(lab_control(connect, LabAccess(PgAccessStore(connect))),
                   PgControlStore(connect),
                   OperatorSession(ops=None, principal=principal))


def lab_traces(settings, connect, sessions, access):
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
    from ..gateway.routes.lab_traces import ClickHouseTraceRows, LabTraces, PgServing
    client = clickhouse_connect.get_client(dsn=limits.clickhouse_url)
    objects = S3ObjectStore.connect(limits.s3_trace_bucket, "infrx/",
                                    settings.deployment.s3_endpoint_url)
    retention = Retention(ClickHouseRetentionStore(client), ClickHouseProjection(client),
                          ClickHouseFeedbackProjection(client), objects,
                          content_days=limits.trace_content_max_days,
                          metadata_months=limits.trace_metadata_months)
    return LabTraces(sessions, access, PgServing(connect), ClickHouseTraceRows(client),
                     retention)
