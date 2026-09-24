"""Conformance factories for the fakes: the reference implementation of the
`Harness` contract every real adapter's factory must also satisfy.

Read one of these next to your own factory when wiring a real adapter (D to
PostgreSQL, Q to Valkey, T to the spool, M to object storage).
"""
from __future__ import annotations

from decimal import Decimal

from .. import money
from ..conformance import Harness
from ..conformance import builders as b
from ..limits import DEFAULTS, PilotSettings
from ..records import FeedbackChannel
from ..conformance.v2_fakes import fake_v2_harness
from ..v2 import fixtures as v2fix
from .engine import EngineFault, FakeEngine
from .feedback import FakeFeedbackService
from .judge import FakeJudgeCoordinator
from .media import FakeMediaStore
from .scheduling import FakeScheduler
from .state import FakeJobStore, FakeStreamStore
from .support import FailurePlan, FakeClock, SequentialIds
from .traces import FakeTraceSink


def _jobstore(limits: PilotSettings | None = None):
    clock, ids, failures = FakeClock(), SequentialIds(), FailurePlan()
    jobs = FakeJobStore(clock, ids, limits=limits or DEFAULTS, failures=failures,
                        # r1 R45: a store starts with the price table the builders' model
                        # is priced at, because the price is the *store's* fact and a
                        # request no longer carries one. A real factory seeds its
                        # `price_versions` rows here instead; `set_price` moves them.
                        prices={b.MODEL: b.DEFAULT_PRICE})
    return jobs, clock, ids, failures


def _job_hooks(jobs: FakeJobStore, stream: FakeStreamStore | None = None) -> dict:
    from ..records import ChunkEventType, EngineEvent

    # One journal per store, always: settlement writes its terminal event through
    # the store's registered journal, so a throwaway per call would lose chunks.
    stream = stream or FakeStreamStore(jobs)

    async def publish(lease):
        """Commit one chunk, i.e. set the publication marker."""
        return await stream.append(lease, (EngineEvent(type=ChunkEventType.delta,
                                                       payload={"content": "x"}),))

    def balance(org_id):
        wallet = jobs.wallet(org_id)
        return {"ledger": wallet.ledger_total, "reserved": wallet.reserved_total,
                "available": wallet.available, "zero": money.ZERO}

    def retune(**changes):
        """Reconfigure the live store, the way an operator changing an environment
        variable would. Accepted jobs must not notice."""
        jobs.limits = jobs.limits.replace(**changes)
        if stream is not None:
            stream.limits = jobs.limits
        return jobs.limits

    return {
        "grant": jobs.grant,
        "balance": balance,
        "active_jobs": jobs.active_jobs,
        "outbox": lambda aggregate_id=None: [event for event in jobs.outbox
                                             if aggregate_id in (None, event.aggregate_id)],
        "outbox_kinds": jobs.outbox_kinds,
        "publish": publish,
        "journal_bytes": jobs.journal.total,
        "unsettleable": lambda: dict(jobs.unsettleable),
        "revoke_key": jobs.revoked_keys.add,
        "unrevoke_key": jobs.revoked_keys.discard,
        "suspend_org": jobs.suspended_orgs.add,
        # r1 R10: the injectable entitlement source, rechecked inside admit.
        "unentitle": jobs.unentitle,
        "entitle": jobs.entitle,
        # r1 R45: the injectable price source. `set_price(model_revision, snapshot)`
        # writes one; `snapshot=None` withdraws it, which is how a case makes a model
        # unpriced without inventing a request field.
        "set_price": jobs.set_price,
        # r1 R4: change the store's *current* configuration, to prove an accepted
        # job keeps the budgets snapshotted at admission.
        "retune": retune,
    }


def jobstore_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    jobs, clock, ids, failures = _jobstore(limits)
    stream = FakeStreamStore(jobs, failures=failures)
    hooks = _job_hooks(jobs, stream)
    # The journal half of the same store, for the cases that must prove a rule holds
    # for `append` as well as for the JobStore's own operations.
    hooks["stream"] = stream
    return Harness(port=jobs, clock=clock, ids=ids, failures=failures, extra=hooks)


def credit_jobstore_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    """contracts v2 (F2P wire-in, item 3): the same fake store, in the CREDIT regime.

    Its trusted rows are the v2 fixture directories (`fake_v2_harness`) plus the two
    fixture key rows (consumer, provider dev). A real factory seeds its key, wallet and
    registry rows instead - D1R's seed is the same fixture set.
    """
    jobs, clock, ids, failures = _jobstore(limits)
    directories = fake_v2_harness()
    jobs.wallet_directory, jobs.catalog = directories.wallets, directories.catalog
    for name in ("auth_context_consumer.json", "auth_context_provider_dev.json"):
        jobs.register_credential(v2fix.BUILDERS[name]())
    stream = FakeStreamStore(jobs, failures=failures)
    hooks = _job_hooks(jobs, stream)

    for wallet in (*directories.wallets.by_user.values(),
                   *directories.wallets.by_provider.values()):
        jobs.seed_credit_wallet(wallet)

    def credit_balance(wallet_id):
        wallet = jobs.credit_wallet(wallet_id)
        return {"ledger": wallet.ledger_total, "reserved": wallet.reserved_total,
                "available": wallet.available}

    hooks.update(credit_balance=credit_balance, credit_grant=jobs.credit_grant,
                 register_credential=jobs.register_credential,
                 publish_rate_card=directories.catalog.publish, stream=stream)
    return Harness(port=jobs, clock=clock, ids=ids, failures=failures, extra=hooks)


def streamstore_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    jobs, clock, ids, failures = _jobstore(limits)
    stream = FakeStreamStore(jobs, failures=failures)
    hooks = _job_hooks(jobs, stream)
    hooks["jobs"] = jobs
    return Harness(port=stream, clock=clock, ids=ids, failures=failures, extra=hooks)


def mediastore_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    clock, ids, failures = FakeClock(), SequentialIds(), FailurePlan()
    store = FakeMediaStore(clock, ids, limits=limits or DEFAULTS, failures=failures)
    return Harness(port=store, clock=clock, ids=ids, failures=failures,
                   # r1 R55: the job row `attach` reads the organization from. A real
                   # adapter joins `jobs`; this is the same lookup, injected.
                   extra={"put_object": store.put_object, "admitted": store.admitted,
                          "materialized": store.materialized,
                          # MPILOT: the fake's state IS its durable state, so another
                          # process sees this very store.
                          "reopened": lambda: store})


def scheduler_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    jobs, clock, ids, failures = _jobstore(limits)
    scheduler = FakeScheduler(clock, limits=limits or DEFAULTS, failures=failures)
    hooks = _job_hooks(jobs)
    hooks["jobs"] = jobs
    return Harness(port=scheduler, clock=clock, ids=ids, failures=failures, extra=hooks)


def engine_factory(limits: PilotSettings | None = None, *, fault: str = "none",
                   **_: object) -> Harness:
    clock, ids, failures = FakeClock(), SequentialIds(), FailurePlan()
    engine = FakeEngine(clock=clock, limits=limits or DEFAULTS, fault=EngineFault(fault),
                        failures=failures)
    return Harness(port=engine, clock=clock, ids=ids, failures=failures,
                   extra={"text": engine.text, "fault": fault})


def tracesink_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    clock, ids, failures = FakeClock(), SequentialIds(), FailurePlan()
    sink = FakeTraceSink(clock, limits=limits or DEFAULTS, failures=failures)
    return Harness(port=sink, clock=clock, ids=ids, failures=failures,
                   extra={"queued": lambda: list(sink.queued), "crash": sink.crash,
                          "content_budget": lambda: sink.content_budget,
                          # r1 R37: the reaper a real sink runs on a timer
                          "reap": sink.reap})


def feedback_factory(limits: PilotSettings | None = None, *,
                     channel: FeedbackChannel = FeedbackChannel.api, **_: object) -> Harness:
    jobs, clock, ids, failures = _jobstore(limits)
    service = FakeFeedbackService(jobs, channel=channel, failures=failures)
    hooks = _job_hooks(jobs)
    hooks["jobs"] = jobs
    hooks["outbox"] = lambda: list(service.outbox)
    hooks["audit"] = lambda: list(service.audit)
    return Harness(port=service, clock=clock, ids=ids, failures=failures, extra=hooks)


def judge_factory(limits: PilotSettings | None = None, *, judge_mode: str = "dry_run",
                  budget: str = "0", **_: object) -> Harness:
    clock, ids, failures = FakeClock(), SequentialIds(), FailurePlan()
    limits = (limits or DEFAULTS).replace(judge_mode=judge_mode,
                                          judge_live_budget_usd=Decimal(budget))
    coordinator = FakeJudgeCoordinator(clock, ids, limits=limits, failures=failures)
    return Harness(port=coordinator, clock=clock, ids=ids, failures=failures,
                   extra={"runs": lambda: dict(coordinator.runs),
                          "available": coordinator.available,
                          # r1 R9: the current consent source `begin_submit` reads.
                          "set_consent": coordinator.set_consent,
                          "revoke_consent": coordinator.revoke_consent,
                          "audit": lambda: list(coordinator.audit)})


FACTORIES = {
    "jobstore": jobstore_factory,
    "streamstore": streamstore_factory,
    "mediastore": mediastore_factory,
    "scheduler": scheduler_factory,
    "engine": engine_factory,
    "tracesink": tracesink_factory,
    "feedback": feedback_factory,
    "judge": judge_factory,
}

# contracts v2 (F2P wire-in, item 3): beside the v1 factories, not in `FACTORIES`, because
# `v2` returns a `V2Harness` of trusted directories rather than a port `Harness`.
def lifecycle_factory(limits: PilotSettings | None = None, **_: object) -> Harness:
    """F2C.a: the three lifecycle ports over the CREDIT fake store. Test-sized windows
    (claim 30 s, grace 60 s, retention 10 min, upload window 1 h); the cases read every instant off
    a returned record, so a real adapter's configured windows work unchanged."""
    from .lifecycle import FakeLifecycle
    credit = credit_jobstore_factory(limits)
    jobs = credit.port
    store = FakeLifecycle(jobs, credit.clock, credit.ids, upload_ttl_s=3600.0, grace_s=60.0,
                          claim_ttl_s=30.0, retention_s=600.0)

    def set_capability(serving_version_id: str, input_modalities: tuple[str, ...]) -> None:
        serving = jobs.catalog.servings[serving_version_id]
        capability = serving.capability.model_dump(mode="json")
        jobs.catalog.servings[serving_version_id] = type(serving).model_validate({
            **serving.model_dump(mode="json"),
            "capability": {**capability, "input_modalities": list(input_modalities)}})

    hooks = {"reopen": store.reopen, "jobs": jobs, "set_capability": set_capability,
             "credit_balance": credit.extra["credit_balance"], "retune": credit.extra["retune"]}
    return Harness(port=store, clock=credit.clock, ids=credit.ids, failures=credit.failures,
                   extra=hooks)


V2_FACTORIES = {
    "v2": fake_v2_harness,
    "credit_jobstore": credit_jobstore_factory,
    "lifecycle": lifecycle_factory,
}
