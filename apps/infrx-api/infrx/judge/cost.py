"""Worst-case cost of a judge run, and the guard that keeps money from leaving.

02: "Reserve maximum output including configured reasoning tokens using versioned
provider rates; include outstanding runs in available budget. Pricing estimates
without a hard maximum cannot authorize live submission."

So this module computes a **reservation**, not a forecast: the configured input,
output *and* reasoning ceilings at a versioned rate, rounded up. Three consequences
worth stating, because each one is a bug somebody would otherwise ship:

* **Reasoning tokens are output tokens.** Adaptive thinking is billed as output
  (`research/traces/06` §3.8), so a ceiling that counts only `max_tokens` under-reserves
  by roughly the thinking budget.
* **No batch discount in a reservation.** `06` §3.8 prices batches at x0.5, and
  halving a worst case turns it into an average. The discount belongs to
  reconciliation (J2's `settle`), where the actual usage is known.
* **Rates are injected and come from an approved source only** (r1 R45 is the same
  rule for customer prices). `research/cross-cutting/cloud-pricing.md` is the one
  approved price document and it prices **GPU capacity**, not provider tokens: it has
  no per-MTok row for any judge model. So the shipped table is **empty**, every
  estimate is unpriced, and `require_live_submission` refuses. That is the honest
  state, and it keeps J1's dry run useful while the row is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ..contracts import errors, money
from ..contracts.limits import PilotSettings

#: What an estimate says when the approved price document has no row for the model.
#: The marker is `research/METHODOLOGY.md`'s: an unknown is labelled, never guessed.
UNPRICED_NOTE = ("⚠️ TO BE VERIFIED: research/cross-cutting/cloud-pricing.md has no "
                 "per-MTok rate row for this judge model; live submission stays disabled "
                 "until an approved price row is injected")


@dataclass(frozen=True)
class ProviderRate:
    """A versioned provider rate. `source` is where the numbers came from, because a
    rate with no provenance is exactly what R45 exists to keep out of the system."""

    price_version: str
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    source: str

    def __post_init__(self) -> None:
        if not self.price_version or not self.model or not self.source:
            raise ValueError("a provider rate needs a price version, a model and a source")
        for name in ("input_per_million", "output_per_million"):
            # `money.parse` is the trust boundary: no floats, exponents, NaN or
            # over-scale values, and a negative rate would make output a credit.
            rate = money.parse(getattr(self, name))
            if rate < 0:
                raise ValueError(f"{name} must not be negative")
            object.__setattr__(self, name, rate)


@dataclass(frozen=True)
class TokenCeilings:
    """The configured hard maxima a reservation is computed from."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "reasoning_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.output_tokens <= 0:
            # A zero output ceiling reserves nothing and then bills whatever arrives -
            # the same defect r1 R55 closed at admission.
            raise ValueError("output_tokens must be positive")

    @property
    def billed_output_tokens(self) -> int:
        """Reasoning tokens are billed as output (`06` §3.8)."""
        return self.output_tokens + self.reasoning_tokens


class RateTable(Protocol):
    """Injected price source, mirroring `JobStore`'s `price_for` (r1 R45)."""

    def rate_for(self, model: str, at: datetime) -> ProviderRate | None:
        ...


@dataclass(frozen=True)
class StaticRateTable:
    """A rate table from approved rows. Empty is the default and a legitimate state:
    it means "no approved price", which fails closed rather than guessing."""

    rows: tuple[ProviderRate, ...] = ()

    def rate_for(self, model: str, at: datetime) -> ProviderRate | None:
        for row in self.rows:
            if row.model == model:
                return row
        return None


#: The approved table as of this task: **empty**, see the module docstring.
APPROVED_RATES = StaticRateTable()


@dataclass(frozen=True)
class CostEstimate:
    model: str
    samples: int
    ceilings: TokenCeilings
    price_version: str | None = None
    per_sample: Decimal | None = None
    worst_case_total: Decimal | None = None
    note: str = ""

    @property
    def priced(self) -> bool:
        return self.per_sample is not None


def estimate_worst_case(rates: RateTable, *, model: str, samples: int,
                        ceilings: TokenCeilings, at: datetime) -> CostEstimate:
    """The reservation a live run would need. Unpriced models answer, they do not
    raise: a dry run's job is to report what it would cost *and* that it cannot."""
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
        raise ValueError("samples must be a nonnegative integer")
    rate = rates.rate_for(model, at)
    if rate is None:
        return CostEstimate(model=model, samples=samples, ceilings=ceilings, note=UNPRICED_NOTE)
    per_sample = money.maximum_hold(ceilings.input_tokens, ceilings.billed_output_tokens,
                                    rate.input_per_million, rate.output_per_million)
    return CostEstimate(model=model, samples=samples, ceilings=ceilings,
                        price_version=rate.price_version, per_sample=per_sample,
                        worst_case_total=money.ceiling(per_sample * samples),
                        note=f"worst case at {rate.price_version} ({rate.source})")


def require_live_submission(settings: PilotSettings, estimate: CostEstimate) -> None:
    """The guard before any provider client is constructed. Raises unless *every*
    condition holds, so `dry_run`, a zero budget and an unpriced model each keep the
    `anthropic` import off the path (02: default mode dry-run, default budget zero).
    """
    if settings.judge_mode != "live":
        raise errors.BudgetExceeded(f"judge mode {settings.judge_mode} cannot authorize a "
                                    "live submission")
    budget = money.parse(settings.judge_live_budget_usd)
    if budget <= 0:
        raise errors.BudgetExceeded("a live submission needs a positive JUDGE_LIVE_BUDGET_USD")
    if not estimate.priced:
        raise errors.BudgetExceeded("an unpriced estimate cannot authorize a live submission")
    total = estimate.worst_case_total or money.ZERO
    if total > budget:
        raise errors.BudgetExceeded(f"the worst case {total} exceeds the live budget {budget}")


def live_submission_allowed(settings: PilotSettings, estimate: CostEstimate) -> bool:
    """`require_live_submission` as a predicate, for a report that must not raise."""
    try:
        require_live_submission(settings, estimate)
    except errors.BudgetExceeded:
        return False
    return True
