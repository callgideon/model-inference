"""In-memory JudgeCoordinator.

Money leaves the platform here, so the defaults are hostile: dry run, zero live
budget. A reservation is the worst case, outstanding and ambiguous runs count
against the budget, a run has exactly one submission intent, and an ambiguous
outcome is never resolved by creating a second billable batch.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .. import errors, money
from ..limits import DEFAULTS, PilotSettings
from ..records import ConsentSnapshot, JudgeRun, JudgeRunState
from .support import FailurePlan, FakeClock, SequentialIds, failure_hooks

# A reservation is still outstanding in all of these: the money is not free yet.
OUTSTANDING_STATES = frozenset({JudgeRunState.reserved, JudgeRunState.submitting,
                                JudgeRunState.submitted, JudgeRunState.ambiguous,
                                JudgeRunState.collecting, JudgeRunState.quarantined})
# States that must never produce a second submission.
FROZEN_STATES = frozenset({JudgeRunState.ambiguous, JudgeRunState.quarantined,
                           JudgeRunState.settled, JudgeRunState.cancelled})


class FakeJudgeCoordinator:
    """`ports.JudgeCoordinator`."""

    def __init__(self, clock: FakeClock | None = None, ids: SequentialIds | None = None, *,
                 limits: PilotSettings = DEFAULTS, failures: FailurePlan | None = None) -> None:
        self.clock = clock or FakeClock()
        self.ids = ids or SequentialIds()
        self.limits = limits
        self.failures = failure_hooks(failures)
        self.runs: dict[str, JudgeRun] = {}
        self.settled_total: Decimal = money.ZERO

    @property
    def budget(self) -> Decimal:
        return money.parse(self.limits.judge_live_budget_usd)

    def outstanding(self) -> Decimal:
        total = money.ZERO
        for run in self.runs.values():
            if run.state in OUTSTANDING_STATES:
                total = total + run.reserved_cost
        return total

    def available(self) -> Decimal:
        return self.budget - self.settled_total - self.outstanding()

    async def reserve(self, run: JudgeRun, consent: ConsentSnapshot, max_cost: Decimal) -> JudgeRun:
        self.failures.before("reserve")
        now: datetime = self.clock.now()
        max_cost = money.parse(max_cost)
        if not consent.allows_evaluation(now):
            raise errors.ConsentMissing(f"org {consent.org_id} has no current evaluation consent")
        if self.limits.judge_mode != "live":
            # A dry-run estimate reserves nothing and cannot authorize submission.
            stored = run.model_copy(update={"state": JudgeRunState.dry_run,
                                            "reserved_cost": money.ZERO, "consent": consent})
            self.runs[run.run_id] = stored
            return stored
        if max_cost <= 0:
            raise errors.BudgetExceeded("a live run needs a positive worst-case reservation")
        if max_cost > self.available():
            raise errors.BudgetExceeded(
                f"reserving {max_cost} exceeds the remaining live budget {self.available()}")
        stored = run.model_copy(update={"state": JudgeRunState.reserved,
                                        "reserved_cost": max_cost, "consent": consent})
        self.runs[run.run_id] = stored
        return stored

    def _run(self, run_id: str) -> JudgeRun:
        run = self.runs.get(run_id)
        if run is None:
            raise errors.NotFound(f"no judge run {run_id}")
        return run

    async def begin_submit(self, run_id: str) -> JudgeRun:
        """One intent per run. Repeating returns the same one; an ambiguous or
        quarantined run refuses, because a retry could buy a second batch."""
        self.failures.before("begin_submit")
        run = self._run(run_id)
        if run.state in FROZEN_STATES:
            raise errors.AmbiguousSubmission(
                f"run {run_id} is {run.state}: resolve it with provider evidence, do not resubmit")
        if run.state is JudgeRunState.dry_run:
            raise errors.BudgetExceeded("dry-run mode cannot authorize a live submission")
        if run.state is JudgeRunState.submitting and run.submit_intent is not None:
            return run
        if run.state is not JudgeRunState.reserved:
            raise errors.Conflict(f"run {run_id} is {run.state}, not reserved")
        run = run.model_copy(update={"state": JudgeRunState.submitting,
                                     "submit_intent": self.ids.uuid()})
        self.runs[run_id] = run
        return run

    async def record_submission(self, run_id: str, external_id: str) -> JudgeRun:
        self.failures.before("record_submission")
        run = self._run(run_id)
        if run.external_batch_id is not None:
            if run.external_batch_id != external_id:
                raise errors.AmbiguousSubmission(
                    f"run {run_id} already has provider batch {run.external_batch_id}")
            return run
        if run.state is not JudgeRunState.submitting:
            raise errors.Conflict(f"run {run_id} is {run.state}, not submitting")
        run = run.model_copy(update={"state": JudgeRunState.submitted,
                                     "external_batch_id": external_id})
        self.runs[run_id] = run
        return run

    async def settle(self, run_id: str, actual: Decimal) -> JudgeRun:
        self.failures.before("settle")
        run = self._run(run_id)
        actual = money.parse(actual)
        if run.state is JudgeRunState.settled:
            return run
        if run.state not in (JudgeRunState.submitted, JudgeRunState.collecting):
            raise errors.Conflict(f"run {run_id} is {run.state} and cannot settle")
        if actual > run.reserved_cost:
            raise errors.BudgetExceeded(
                f"actual {actual} exceeds the {run.reserved_cost} reserved for run {run_id}")
        self.settled_total = self.settled_total + actual
        run = run.model_copy(update={"state": JudgeRunState.settled, "actual_cost": actual,
                                     "reserved_cost": money.ZERO,
                                     "reconciled_at": self.clock.now()})
        self.runs[run_id] = run
        return run

    async def quarantine(self, run_id: str, reason: str) -> JudgeRun:
        """Hold the reservation and stop automatic retries. Without a provider id
        the outcome is `ambiguous`; with one it is `quarantined`."""
        self.failures.before("quarantine")
        run = self._run(run_id)
        state = (JudgeRunState.quarantined if run.external_batch_id is not None
                 else JudgeRunState.ambiguous)
        run = run.model_copy(update={"state": state, "reconciled_at": None})
        self.runs[run_id] = run
        return run

    async def collect(self, run_id: str) -> JudgeRun:
        """Not a port operation: the fake's way to reach `collecting` before settle."""
        run = self._run(run_id)
        if run.state is not JudgeRunState.submitted:
            raise errors.Conflict(f"run {run_id} is {run.state}, not submitted")
        run = run.model_copy(update={"state": JudgeRunState.collecting})
        self.runs[run_id] = run
        return run
