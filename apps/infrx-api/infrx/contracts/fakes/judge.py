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
from ..records import (AuthContext, ConsentSnapshot, JudgeResolution, JudgeRun, JudgeRunState, Role)
from .support import FailurePlan, FakeClock, SequentialIds, failure_hooks, money_input

# A reservation is still outstanding in all of these: the money is not free yet.
OUTSTANDING_STATES = frozenset({JudgeRunState.reserved, JudgeRunState.submitting,
                                JudgeRunState.submitted, JudgeRunState.ambiguous,
                                JudgeRunState.collecting, JudgeRunState.quarantined})
# States that must never produce a second submission.
FROZEN_STATES = frozenset({JudgeRunState.ambiguous, JudgeRunState.quarantined,
                           JudgeRunState.settled, JudgeRunState.cancelled})
# Closed: the money is resolved and the run is over. Nothing reopens these.
CLOSED_STATES = frozenset({JudgeRunState.settled, JudgeRunState.cancelled,
                             JudgeRunState.quarantined})


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
        # r1 R9: the *current* consent record per org, which is what `begin_submit`
        # reads. A real adapter queries `consent_history`; this stands for it, so a
        # revocation after the reservation is expressible and reaches egress.
        self.consent_records: dict[str, ConsentSnapshot] = {}
        self.audit: list[dict] = []          # append-only, r1 R8

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

    # --- test hooks (not part of the port) -----------------------------------
    def set_consent(self, consent: ConsentSnapshot) -> None:
        """Record the org's current consent, as the consent-history table does."""
        self.consent_records[consent.org_id] = consent

    def revoke_consent(self, org_id: str, at: datetime | None = None) -> ConsentSnapshot:
        """What the customer does in the console. The stored snapshot a run was
        reserved with is untouched, exactly as in PostgreSQL."""
        current = self.consent_records.get(org_id)
        if current is None:
            raise KeyError(f"no consent recorded for org {org_id}")
        revoked = current.model_copy(update={"revoked_at": at or self.clock.now(),
                                             "consent_version": current.consent_version + 1})
        self.consent_records[org_id] = revoked
        return revoked

    def current_consent(self, run: JudgeRun) -> ConsentSnapshot:
        """The consent that authorizes egress *now*, not the one reserved with."""
        return self.consent_records.get(run.org_id, run.consent)

    async def reserve(self, run: JudgeRun, consent: ConsentSnapshot, max_cost: Decimal) -> JudgeRun:
        self.failures.before("reserve")
        now: datetime = self.clock.now()
        # r1 R11: a judge cost is a monetary input, in every mode.
        max_cost = money_input(max_cost, "the worst-case judge cost")
        if consent.org_id != run.org_id:
            # r1 R10: one org's evaluation consent never authorizes another org's
            # traces leaving the platform.
            raise errors.NotFound(f"consent for org {consent.org_id} does not match run "
                                  f"{run.run_id}")
        stored = self.runs.get(run.run_id)
        if stored is not None:
            # One reservation per run (01: "duplicate calls produce one run/intent").
            # Re-reserving must not reset the state, mint a second submission intent
            # or buy a second provider batch for an ambiguous run.
            return stored
        if not consent.allows_evaluation(now):
            raise errors.ConsentMissing(f"org {consent.org_id} has no current evaluation consent")
        current = self.consent_records.get(run.org_id)
        if current is not None and not current.allows_evaluation(now):
            # A stale snapshot never outvotes the recorded current consent.
            raise errors.ConsentMissing(f"org {run.org_id} has revoked evaluation consent")
        if current is None:
            self.consent_records[run.org_id] = consent
        # A caller-supplied intent or provider id is not a fact: reservation is the
        # start of this run's life, so both are reset here.
        fresh = {"consent": consent, "submit_intent": None, "external_batch_id": None,
                 "actual_cost": None, "reconciled_at": None}
        if self.limits.judge_mode != "live":
            # A dry-run estimate reserves nothing and cannot authorize submission.
            stored = run.model_copy(update={"state": JudgeRunState.dry_run,
                                            "reserved_cost": money.ZERO, **fresh})
            self.runs[run.run_id] = stored
            return stored
        if max_cost <= 0:
            raise errors.BudgetExceeded("a live run needs a positive worst-case reservation")
        if max_cost > self.available():
            raise errors.BudgetExceeded(
                f"reserving {max_cost} exceeds the remaining live budget {self.available()}")
        stored = run.model_copy(update={"state": JudgeRunState.reserved,
                                        "reserved_cost": max_cost, **fresh})
        self.runs[run.run_id] = stored
        return stored

    def _run(self, run_id: str) -> JudgeRun:
        run = self.runs.get(run_id)
        if run is None:
            raise errors.NotFound(f"no judge run {run_id}")
        return run

    def _release(self, run: JudgeRun, state: JudgeRunState, **update) -> JudgeRun:
        """Free the reservation: the money stops counting against the budget."""
        run = run.model_copy(update={"state": state, "reserved_cost": money.ZERO, **update})
        self.runs[run.run_id] = run
        return run

    async def begin_submit(self, run_id: str) -> JudgeRun:
        """One intent per run. Repeating returns the same one; an ambiguous or
        quarantined run refuses, because a retry could buy a second batch."""
        self.failures.before("begin_submit")
        run = self._run(run_id)
        if run.state in FROZEN_STATES:
            raise errors.AmbiguousSubmission(
                f"run {run_id} is {run.state}: resolve it with provider evidence, do not resubmit")
        now = self.clock.now()
        consent = self.current_consent(run)
        if not consent.allows_evaluation(now):
            # r1 R9 / 02: consent must be current *at submission*, checked against
            # the current consent record and not only the snapshot reserved with.
            # Revocation blocks egress and releases the reservation.
            self._release(run, JudgeRunState.cancelled,
                          reconciled_at=now, consent=consent)
            self.audit.append({"run_id": run_id, "at": now, "event": "consent_revoked",
                               "consent_version": consent.consent_version})
            raise errors.ConsentMissing(
                f"run {run_id} has no current evaluation consent at submission time")
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
        if not isinstance(external_id, str) or not external_id.strip():
            # An empty provider id is exactly the ambiguity this operation exists to
            # remove; recording one would hide it.
            raise errors.InvalidRequest("a provider batch id is required")
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
        # r1 R11: a negative actual cost would *raise* the remaining budget, so the
        # next reservation could exceed the configured cap. Refused at the boundary.
        actual = money_input(actual, "the actual judge cost")
        if run.state is JudgeRunState.settled:
            return run
        if run.state not in (JudgeRunState.submitted, JudgeRunState.collecting):
            raise errors.Conflict(f"run {run_id} is {run.state} and cannot settle")
        if actual > run.reserved_cost:
            raise errors.BudgetExceeded(
                f"actual {actual} exceeds the {run.reserved_cost} reserved for run {run_id}")
        self.settled_total = self.settled_total + actual
        run = self._release(run, JudgeRunState.settled, actual_cost=actual,
                            reconciled_at=self.clock.now())
        return run

    async def quarantine(self, run_id: str, reason: str) -> JudgeRun:
        """Hold the reservation and stop automatic retries. Without a provider id
        the outcome is `ambiguous`; with one it is `quarantined`."""
        self.failures.before("quarantine")
        run = self._run(run_id)
        if run.state in CLOSED_STATES:
            # A resolved run is closed: reopening a settled one would double count
            # its money and erase the audit trail.
            raise errors.Conflict(f"run {run_id} is already {run.state}")
        state = (JudgeRunState.quarantined if run.external_batch_id is not None
                 else JudgeRunState.ambiguous)
        run = run.model_copy(update={"state": state, "reconciled_at": None})
        self.runs[run_id] = run
        self.audit.append({"run_id": run_id, "at": self.clock.now(), "event": "quarantine",
                           "reason": reason, "state": state.value})
        return run

    async def resolve_ambiguous(self, run_id: str, operator: AuthContext,
                                resolution: JudgeResolution, reason: str, *,
                                external_id: str | None = None) -> JudgeRun:
        """r1 R8: the operator-only way out of `ambiguous`, with an audit record and
        never a second submission."""
        self.failures.before("resolve_ambiguous")
        run = self._run(run_id)
        if operator is None or operator.role is not Role.operator:
            raise errors.Forbidden("resolving an ambiguous run requires a platform operator")
        if not reason or not reason.strip():
            raise errors.InvalidRequest("a resolution needs a reason for the audit record")
        resolution = JudgeResolution(resolution)
        if run.state is not JudgeRunState.ambiguous:
            raise errors.Conflict(f"run {run_id} is {run.state}, not ambiguous")
        intent = run.submit_intent
        if resolution is JudgeResolution.adopt_provider_evidence:
            if not external_id or not external_id.strip():
                raise errors.InvalidRequest(
                    "adopting provider evidence requires the discovered provider id")
            resolved = run.model_copy(update={"state": JudgeRunState.collecting,
                                              "external_batch_id": external_id})
            self.runs[run_id] = resolved
        else:
            resolved = self._release(run, JudgeRunState.quarantined,
                                     reconciled_at=self.clock.now())
        assert resolved.submit_intent == intent      # never a second submission
        self.audit.append({"run_id": run_id, "at": self.clock.now(),
                           "event": "resolve_ambiguous", "resolution": resolution.value,
                           "reason": reason, "operator": operator.principal,
                           "external_batch_id": resolved.external_batch_id})
        return resolved

    async def collect(self, run_id: str) -> JudgeRun:
        """Not a port operation: the fake's way to reach `collecting` before settle."""
        run = self._run(run_id)
        if run.state is not JudgeRunState.submitted:
            raise errors.Conflict(f"run {run_id} is {run.state}, not submitted")
        run = run.model_copy(update={"state": JudgeRunState.collecting})
        self.runs[run_id] = run
        return run
