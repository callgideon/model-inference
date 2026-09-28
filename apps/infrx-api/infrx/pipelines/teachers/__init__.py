"""P2: bounded teacher annotation batches (PIPELINE-BUDGET, PIPELINE-LINEAGE).

**A batch is a manifest over one N2 dataset version.** `plan` (the dry run: no rate needed,
no ledger, no egress) reads the version and cuts its train and validation samples, in id
order, into chunks of `chunk_size`; the holdout is never teacher-labelled, so evaluation truth
and training labels stay apart. Each chunk is a D6J run whose id is derived from the batch id
and its samples, so a resumed worker addresses the same runs and the same samples - chunk
boundaries never move with the rights, so a sample that already left is never sent again.
Each chunk's worst case is priced at the rate effective at `now` (an upper bound); without an
approved rate the plan is still a plan, just unpriced. The plan also lists the samples the
provider may not **currently** send to an external model and train on (`external_judging` and
`training`, read at every call like N2's exports): a live run skips them.

**Live runs are J2's protocol, not a second one** (`judge.submit`): the same live-mode switch
(R57), the provider's own named PROVIDER_USD payer, one D6J reservation per chunk against its
budget (never CREDIT, never the consumer's money), one submit intent per run, and egress only
through `judge.submit.send` - the one path that rechecks the permission immediately before
egress, releases a definite rejection and quarantines an unknown outcome with its hold kept
(`judge.submit.reconcile` resolves it from provider evidence). The adapter is J2's
`HttpJudgeProvider`, which refuses every host but the local fake until P-10.

A chunk the budget cannot cover stops the batch there (`stopped="budget"`): nothing after it is
reserved or sent. A permission withdrawn before a chunk leaves releases that chunk's hold and
stops the batch (`stopped="permission"`).

**Collection** reads a submitted run's results once per poll: a label is one short text with
an optional confidence (`parse_label`); a result for a sample the run never sent, a
duplicate, a malformed label or a sample the provider may no longer train on is a per-item
failure, never imported, and recorded in D8's per-item failure log. The rest reconcile into D8 through P1's `import_labels` as method
`model` - P1 publishes those as `synthetic`, never `human` and never ground truth - with the
teacher model and prompt pinned on each row. The run settles once, when the provider is done.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ...contracts import errors
from ...contracts.limits import MAX_FEEDBACK_TEXT_CHARS, PilotSettings
from ...contracts.v2.money_units import ProviderUsd
from ...contracts.v2.records import ProviderCapability
from ...datasets.imports import sample_key
from ...judge.cost import JUDGE_MODE_LIVE, RateTable, TokenCeilings, worst_case
from ...judge.dryrun import DEFAULT_CEILINGS, MAX_CANDIDATES
from ...judge.submit import (ConsentRef, JudgeLedger, JudgeProvider, LedgerRun, _run,
                             require_own_payer, send)

#: Sending a sample to a teacher is external processing, and its label is for training.
PURPOSES = ("external_judging", "training")
TEACHER_METHOD = "model"          # P1: a pipeline `model` row is published `synthetic`
LABEL_KEYS = frozenset({"label", "confidence"})


@dataclass(frozen=True)
class TeacherBatch:
    batch_id: str                 # minted once per request: the same batch resumes, never repeats
    provider_org_id: str          # server-derived (the guarded workspace)
    requested_by: str             # server-verified user id
    dataset_ref: str              # an N2 version
    rubric_ref: str               # the label rubric P1 records the labels against
    teacher_model: str            # the rate row
    prompt_version: str           # pinned on every label
    payer_ref: str
    chunk_size: int = 50
    ceilings: TokenCeilings = field(default=DEFAULT_CEILINGS)


@dataclass(frozen=True)
class TeacherWiring:
    members: Any                  # the L2 AccessStore (`membership`, `db_now`)
    ledger: JudgeLedger           # D8's PgTeacherLedger: J2's ledger + `record_failures`
    provider: JudgeProvider       # J2's HttpJudgeProvider to the local teacher fake
    store: Any                    # the D7/N2 store (`resolve`, `accessible_samples`)
    objects: Any                  # N2 sample content objects
    labels: Any                   # P1's `import_labels`
    log: Any                      # P1's `LabelLog` (D8)
    rates: RateTable
    settings: PilotSettings
    # P2.a: every sample's content passes through this before egress. Required, no default:
    # N2's redaction helper is not public yet (WR-P2-4), and a teacher host past P-10 must
    # not be wired without one (a gate on P-10, not an option).
    redact: Callable[[str], str]


@dataclass(frozen=True)
class Plan:
    chunks: tuple[tuple[str, tuple[str, ...]], ...]     # (run id, sample ids)
    omitted: tuple[dict, ...]                           # the holdout
    not_permitted: tuple[str, ...]                      # skipped by a live run, as of now
    price_version: str | None
    worst_case: ProviderUsd | None                      # None: no approved rate (unpriced)


@dataclass(frozen=True)
class BatchReport:
    runs: tuple[LedgerRun, ...]
    stopped: str | None = None                          # budget | permission
    unsent: tuple[str, ...] = ()                        # run ids never reserved


@dataclass(frozen=True)
class Collected:
    run: LedgerRun
    imported: Any = None                                # P1's `Imported`
    failures: tuple[tuple[str, str], ...] = ()


async def _allowed(batch: TeacherBatch, store) -> set[str]:
    allowed = None
    for purpose in PURPOSES:
        ids = set(await store.accessible_samples(batch.dataset_ref,
                                                 provider_org_id=batch.provider_org_id,
                                                 purpose=purpose))
        allowed = ids if allowed is None else allowed & ids
    return allowed


async def plan(batch: TeacherBatch, *, store, rates: RateTable, now: datetime) -> Plan:
    """P2.a: the dry run. Reads the version and the current rights; prices when it can."""
    if not 1 <= batch.chunk_size <= MAX_CANDIDATES:
        raise errors.InvalidRequest(f"a chunk is 1..{MAX_CANDIDATES} samples")
    manifest = await store.resolve(batch.dataset_ref, provider_org_id=batch.provider_org_id)
    holdout = set(manifest.splits.holdout)
    allowed = await _allowed(batch, store)
    kept, omitted = [], []
    for sample_id in sorted(s.sample_id for s in manifest.samples):
        if sample_id in holdout:
            omitted.append({"sample_id": sample_id, "reason": "holdout"})
        else:
            kept.append(sample_id)
    cuts = [tuple(kept[start:start + batch.chunk_size])
            for start in range(0, len(kept), batch.chunk_size)]
    chunks = tuple((str(uuid.uuid5(uuid.UUID(batch.batch_id), ",".join(ids))), ids)
                   for ids in cuts)
    rate = rates.rate_for(batch.teacher_model, now)
    total = None
    if rate is not None:
        total = ProviderUsd.zero()
        for _, ids in chunks:
            total = total + ProviderUsd(worst_case(rate, batch.ceilings, len(ids)))
    return Plan(chunks=chunks, omitted=tuple(omitted),
                not_permitted=tuple(i for i in kept if i not in allowed), price_version=rate.price_version if rate else None, worst_case=total)


async def _member(batch: TeacherBatch, members) -> None:
    membership = await members.membership(batch.provider_org_id, batch.requested_by)
    now = await members.db_now()
    if membership is None:
        raise errors.NotFound("no such provider workspace")
    if not membership.permits(ProviderCapability.run_evaluation, now, batch.provider_org_id):
        raise errors.Forbidden("a teacher batch needs a developer or administrator")


async def run_batch(batch: TeacherBatch, *, wiring: TeacherWiring) -> BatchReport:
    """P2.b: every chunk reserved, then sent once through J2's egress path, in order."""
    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:
        raise errors.BudgetExceeded("the judge is not in live mode: nothing is submitted")
    require_own_payer(batch.provider_org_id, batch.payer_ref)
    await _member(batch, wiring.members)
    ledger = wiring.ledger
    now = await ledger.db_now()
    planned = await plan(batch, store=wiring.store, rates=wiring.rates, now=now)
    rate = wiring.rates.rate_for(batch.teacher_model, now)
    if rate is None:
        raise errors.BudgetExceeded(f"{batch.teacher_model} has no approved rate: nothing reserved")
    runs: list[LedgerRun] = []

    async def recheck(ids) -> None:
        if not set(ids) <= await _allowed(batch, wiring.store):
            raise errors.Forbidden("a sample of this chunk may no longer leave")

    for n, (run_id, ids) in enumerate(planned.chunks):
        unsent = tuple(r for r, _ in planned.chunks[n + 1:])
        try:
            run = await ledger.reserve(
                run_id=run_id, provider_org_id=batch.provider_org_id, payer_ref=batch.payer_ref,
                # the consent snapshot of a batch is its dataset version; per-sample grants
                # are in the version's manifest. 0036's reserve/record_sent check
                # lab_access_grants and refuse this ref: SR-P2-1 (amended) asks them to branch
                # on purpose='teacher_annotation' and recheck the version's per-sample rights
                consent=ConsentRef(batch.dataset_ref, 1), sample_ids=ids, media_ids=frozenset(),
                price_version=rate.price_version,
                max_cost=ProviderUsd(worst_case(rate, batch.ceilings, len(ids))))
        except errors.BudgetExceeded:
            return BatchReport(tuple(runs), "budget", (run_id, *unsent))
        run, mine = await ledger.begin_submit(run.run_id)
        if not mine:
            runs.append(run)      # another call holds (or held) this chunk's intent
            continue
        items, allowed = [], await _allowed(batch, wiring.store)
        for sample in await _samples(batch, wiring.store, run.sample_ids):
            if sample.sample_id not in allowed:
                continue          # skipped before egress, like J2's missing content
            body = await wiring.objects.get(sample_key(batch.provider_org_id,
                                                       sample.content_digest))
            if body is not None:
                items.append({"sample_id": sample.sample_id,
                              "content": wiring.redact(json.loads(body)["content"]),
                              "prompt_version": batch.prompt_version})
        try:
            runs.append(await send(ledger, wiring.provider, run, items,
                                   lambda: recheck(item["sample_id"] for item in items)))
        except errors.DomainError:
            runs.append(await ledger.run(run.run_id))
            return BatchReport(tuple(runs), "permission", unsent)
    return BatchReport(tuple(runs))


async def _samples(batch: TeacherBatch, store, ids) -> list:
    manifest = await store.resolve(batch.dataset_ref, provider_org_id=batch.provider_org_id)
    wanted = set(ids)
    return [s for s in manifest.samples if s.sample_id in wanted]


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def parse_label(text: str) -> tuple[str, float | int | None] | None:
    """`{"label": <short text>, "confidence"?: 0..1}`, or None. Never raises."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return None
    if type(payload) is not dict or "label" not in payload or not set(payload) <= LABEL_KEYS:
        return None
    label, confidence = payload["label"], payload.get("confidence")
    if type(label) is not str or not label.strip() or len(label) > MAX_FEEDBACK_TEXT_CHARS:
        return None
    if confidence is not None and (type(confidence) not in (int, float)
                                   or not 0 <= confidence <= 1):
        return None
    return label, confidence


async def collect(batch: TeacherBatch, run_id: str, *, wiring: TeacherWiring) -> Collected:
    """P2.c: import what has arrived (as `model` labels, through P1); settle once when done."""
    ledger = wiring.ledger
    run = await ledger.run(run_id)
    if run is not None and run.state == "completed":
        return Collected(run)
    run = await _run(ledger, run_id, "submitted")
    polled = await wiring.provider.results(run.external_id)
    allowed = await _allowed(batch, wiring.store)
    rows, failures, seen = [], [], set()
    for sample_id, text in polled.items:
        if sample_id not in run.sent_ids:
            failures.append((sample_id, "not_sent"))
            continue
        if sample_id in seen:
            failures.append((sample_id, "duplicate"))
            continue
        seen.add(sample_id)
        if sample_id not in allowed:
            failures.append((sample_id, "grant_not_current"))
            continue
        parsed = parse_label(text)
        if parsed is None:
            failures.append((sample_id, "malformed_label"))
            continue
        row = {"sample_id": sample_id, "method": TEACHER_METHOD,
               "method_version": batch.prompt_version, "label": parsed[0],
               "model": batch.teacher_model, "prompt": batch.prompt_version}
        if parsed[1] is not None:
            row["confidence"] = parsed[1]
        rows.append(row)
    logged = [f for f in failures if _is_uuid(f[0])]   # a provider's id D8 cannot store stays
    if logged:                                          # in the result only (0-LSI2-F1)
        await ledger.record_failures(run_id, logged)    # D8's append-only per-item log
    imported = None
    if rows:
        imported = await wiring.labels(wiring.store, wiring.log,
                                       provider_org_id=batch.provider_org_id,
                                       actor=batch.requested_by, dataset_ref=batch.dataset_ref,
                                       rubric_ref=batch.rubric_ref, rows=rows)
    if polled.done:
        run = await ledger.settle(run_id, polled.cost)
    return Collected(run, imported, tuple(failures))
