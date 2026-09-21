"""The dry run: what would be judged, what it would cost, and why not to submit.

`JUDGE_MODE` defaults to `dry_run` and `JUDGE_LIVE_BUDGET_USD` to `0` (08 §5), so this
is the only judge path that runs at all until an operator changes both. It reaches the
trace source and nothing else: no provider client, no `anthropic` import, no egress -
proved by the import-hygiene cases in `tests/j/test_cost.py`, one of which re-checks it
in a fresh interpreter.

Two orderings here are the point rather than an accident:

* **consent and ownership are checked before the source is read.** A refusal that
  queries the traces first has already touched the data it was refusing to touch, and
  `select`'s own check cannot do it - by then the rows are in memory.
* **the scan bound is enforced here, not requested.** `limit` is validated and the
  returned rows are truncated, because a source that ignores the argument (a query
  without a `LIMIT`, a projection replaying a backlog) would otherwise decide how much
  work and memory this process spends.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Awaitable, Iterable
from itertools import islice

from ..contracts import errors, money
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import ConsentSnapshot, JudgeRun, JudgeRunState
from .cost import (APPROVED_RATES, CostEstimate, RateTable, TokenCeilings, estimate_worst_case,
                   live_submission_allowed)
from .rubric import MARLIN_VIDEO_V1, Rubric
from .sampling import (DEFAULT_DESIGN, CalibrationDesign, CandidateSource, Selection,
                       check_consent, select)

#: `06` §3.4/§3.8's ceilings: ~1,300 text input + 8 frames at 1000 px (756 tokens each),
#: 2,048 output and a 1,024-token thinking allowance, all `est.` and all **ceilings**
#: rather than expectations - which is what a reservation is made of.
DEFAULT_CEILINGS = TokenCeilings(input_tokens=7_348, output_tokens=2_048, reasoning_tokens=1_024)

#: `06` §3.4: the batch item ceiling, and therefore the candidate scan bound.
MAX_CANDIDATES = 200


def scan_bound(limit: object) -> int:
    """A validated candidate scan bound in 1..`MAX_CANDIDATES`.

    `True` is not 1 and `10**9` is not a bound anybody chose; a negative or zero limit
    would ask the source for nothing and then report an empty calibration as a result.
    """
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise errors.InvalidRequest("the candidate scan bound must be an integer")
    if not 1 <= limit <= MAX_CANDIDATES:
        raise errors.InvalidRequest(f"the candidate scan bound must be in 1..{MAX_CANDIDATES}")
    return limit


@dataclass(frozen=True)
class DryRunPlan:
    org_id: str
    rubric_id: str
    rubric_version: int
    model: str
    seed: str
    mode: str
    selection: Selection
    cost: CostEstimate
    live_submission_enabled: bool

    @property
    def sample_ids(self) -> tuple[str, ...]:
        return self.selection.sample_ids

    def judge_run(self, *, run_id: str, consent: ConsentSnapshot, model_revision: str,
                  created_at: datetime) -> JudgeRun:
        """The plan as the frozen `JudgeRun` record (01, r1 R43).

        `state=dry_run` with `reserved_cost=0`: an estimate reserves nothing, so it
        cannot authorize a submission and cannot consume another run's budget. The
        rubric version is the integer the record demands, and the sample ids are unique
        (R56) because the sampler deduplicated the candidates.
        """
        return JudgeRun(run_id=run_id, org_id=self.org_id, sample_ids=self.sample_ids,
                        consent=consent, rubric_version=self.rubric_version,
                        model_revision=model_revision, reserved_cost=money.ZERO,
                        state=JudgeRunState.dry_run, created_at=created_at)


async def plan_dry_run(source: CandidateSource, *, org_id: str, consent: ConsentSnapshot,
                       model: str, since: datetime, now: datetime, seed: str,
                       rubric: Rubric = MARLIN_VIDEO_V1,
                       settings: PilotSettings = DEFAULTS,
                       rates: RateTable = APPROVED_RATES,
                       ceilings: TokenCeilings = DEFAULT_CEILINGS,
                       design: CalibrationDesign = DEFAULT_DESIGN,
                       limit: int = MAX_CANDIDATES) -> DryRunPlan:
    """Select, price and report.

    Raises `NotFound` on a tenant mismatch and `ConsentMissing` without current
    evaluation consent - both **before** the source is touched - and `InvalidRequest`
    for a scan bound that is not one.
    """
    limit = scan_bound(limit)
    check_consent(org_id, consent, now)
    # `islice`, not `tuple(...)[:limit]`: the bound is ours rather than the source's
    # promise, and slicing *after* materializing copied a 100,000-row answer into this
    # process before throwing all but 200 of it away. A generator is consumed only as far
    # as the bound.
    pending = source.candidates(org_id, since=since, limit=limit)
    if not isinstance(pending, Awaitable):
        # An async *generator* is the shape most likely to be written by mistake, and
        # `await`ing one raises `TypeError` from inside this function. Checked rather than
        # caught, so a genuine `TypeError` from the adapter's own query still propagates.
        raise errors.InvalidRequest("the candidate source must be an async function")
    rows = await pending
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Iterable):
        # An async generator, a coroutine somebody forgot to await, a `None` from an adapter
        # that logged instead of returning: a typed refusal rather than a `TypeError` out of
        # `islice`. `str`/`bytes` are iterable and would silently yield characters.
        raise errors.InvalidRequest("the candidate source must answer a synchronous iterable")
    candidates = tuple(islice(rows, limit))
    selection = select(org_id, consent, candidates, rubric_version=rubric.version,
                       seed=seed, now=now, design=design)
    estimate = estimate_worst_case(rates, model=model, samples=len(selection.samples),
                                   ceilings=ceilings, at=now)
    return DryRunPlan(org_id=org_id, rubric_id=rubric.rubric_id, rubric_version=rubric.version,
                      model=model, seed=seed, mode=settings.judge_mode, selection=selection,
                      cost=estimate,
                      live_submission_enabled=live_submission_allowed(settings, estimate,
                                                                     rates=rates, at=now))
