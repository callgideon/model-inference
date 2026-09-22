"""Worst-case cost of a judge run, and the guard that keeps money from leaving.

02: "Reserve maximum output including configured reasoning tokens using versioned
provider rates; include outstanding runs in available budget. Pricing estimates
without a hard maximum cannot authorize live submission."

So this module computes a **reservation**, not a forecast, and ruling **R57** fixes how
the authorization is decided:

* the mode must be **exactly** `live` - not "anything but dry_run", which authorized
  every typo of a mode name;
* the rate must come from the approved table, be finite and positive, be the row
  **effective at the store clock**, and the table must refuse duplicate rows for a
  model (with "first row wins" a stale cheaper row silently under-reserved);
* the guard **recomputes** `ceiling(per_sample x samples)` from the rate and the
  ceilings. It never authorizes against a total handed to it: a `CostEstimate` is a
  report, and a report with `per_sample = 999` and `total = None` (or `-5`, or `0.01`)
  authorized a submission that could spend anything;
* the total must be finite, positive and `<=` the budget, and anything else refuses
  with a typed `DomainError` rather than letting a `decimal.InvalidOperation` escape.

Two standing decisions:

* **Reasoning tokens are output tokens.** Adaptive thinking is billed as output
  (`research/traces/06` §3.8), so a ceiling that counts only `max_tokens` under-reserves
  by roughly the thinking budget.
* **No batch discount in a reservation.** `06` §3.8 prices batches at x0.5, and halving
  a worst case turns it into an average. The discount belongs to reconciliation (J2's
  `settle`), where the actual usage is known.

And **rates are injected from an approved source only** (r1 R45 is the same rule for
customer prices). `research/cross-cutting/cloud-pricing.md` is the one approved price
document and it prices **GPU capacity**, not provider tokens: it has no per-MTok row for
any judge model. So the shipped table is **empty**, every estimate is unpriced, and
`require_live_submission` refuses.
"""
from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ..contracts import errors, money
from ..contracts.limits import PilotSettings

#: R57: the mode is matched exactly, and this is the only accepted value.
JUDGE_MODE_LIVE = "live"

#: What an estimate says when the approved price document has no row for the model.
#: The marker is `research/METHODOLOGY.md`'s: an unknown is labelled, never guessed.
UNPRICED_NOTE = ("⚠️ TO BE VERIFIED: research/cross-cutting/cloud-pricing.md has no "
                 "per-MTok rate row for this judge model; live submission stays disabled "
                 "until an approved price row is injected")


def _positive_money(value: object) -> Decimal | None:
    """A finite, positive `numeric(20, 8)` amount, or `None` for anything else.

    `money.parse` already refuses floats, exponents, `NaN` and over-scale values by
    raising; this is the predicate form, because the guard must *refuse*, not explode.
    """
    try:
        amount = money.parse(value)
    except (ValueError, ArithmeticError, TypeError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


@dataclass(frozen=True)
class ProviderRate:
    """A versioned provider rate, effective from an instant.

    `source` is where the numbers came from, because a rate with no provenance is what
    R45 exists to keep out of the system. `effective_at` is what makes "the rate that
    applied when this run was priced" answerable at all (R57).
    """

    price_version: str
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    source: str
    effective_at: datetime

    def __post_init__(self) -> None:
        for name in ("price_version", "model", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a provider rate needs a {name}")
        for name in ("input_per_million", "output_per_million"):
            # R57: finite and **positive**. A negative rate turns output into a credit,
            # and a zero rate makes a zero worst case that any budget "covers" - both
            # authorize an effectively unbounded submission.
            rate = _positive_money(getattr(self, name))
            if rate is None:
                raise ValueError(f"{name} must be a finite positive decimal amount")
            object.__setattr__(self, name, rate)
        if not isinstance(self.effective_at, datetime) or self.effective_at.tzinfo is None:
            # A naive instant cannot be compared with the store clock, and the database
            # clock is the only clock (08 §2).
            raise ValueError("effective_at must be a UTC-aware datetime")


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
    """Injected price source, mirroring `JobStore`'s `price_for` (r1 R45).

    `rate_for` answers with the row **effective at `at`**, or `None`. It raises a
    `DomainError` only for a caller bug (a naive instant); an unpriced model is `None`,
    never an exception and never a zero.
    """

    def rate_for(self, model: str, at: datetime) -> ProviderRate | None:
        ...


@dataclass(frozen=True)
class StaticRateTable:
    """A rate table from approved rows. Empty is the default and a legitimate state: it
    means "no approved price", which fails closed rather than guessing."""

    rows: tuple[ProviderRate, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(row, ProviderRate) for row in self.rows):
            raise errors.InvalidRequest("a rate table holds ProviderRate rows")
        keys = [(row.model, row.effective_at) for row in self.rows]
        if len(set(keys)) != len(keys):
            # R57: two rows for one model at one instant is an ambiguous price. Which
            # one applied would depend on list order, i.e. on whoever edited last.
            raise errors.InvalidRequest("duplicate provider rate rows for a model and instant")

    def rate_for(self, model: str, at: datetime) -> ProviderRate | None:
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise errors.InvalidRequest("a price lookup needs a UTC-aware instant")
        effective = [row for row in self.rows if row.model == model and row.effective_at <= at]
        if not effective:
            return None
        # R57: the row effective at the store clock, not the first one in the list. With
        # "first wins", adding a newer, dearer row left every reservation at the old
        # rate and under-reserved by the difference.
        return max(effective, key=lambda row: row.effective_at)


#: The approved table as of this task: **empty**, see the module docstring.
APPROVED_RATES = StaticRateTable()


@dataclass(frozen=True)
class CostEstimate:
    """A **report**, never an authorization. `priced` requires all three of a price
    version, a positive per-sample amount and a positive total, because a partially
    filled estimate used to read as priced."""

    model: str
    samples: int
    ceilings: TokenCeilings
    price_version: str | None = None
    per_sample: Decimal | None = None
    worst_case_total: Decimal | None = None
    note: str = ""

    @property
    def priced(self) -> bool:
        # A blank version is not configuration (r1 R51's lesson in this currency), so it
        # is `strip()`ed rather than merely truthiness-tested.
        return (isinstance(self.price_version, str) and bool(self.price_version.strip())
                and _positive_money(self.per_sample) is not None
                and _positive_money(self.worst_case_total) is not None)


def _sample_count(samples: object) -> int:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
        raise ValueError("samples must be a nonnegative integer")
    return samples


def worst_case(rate: ProviderRate, ceilings: TokenCeilings, samples: int) -> Decimal:
    """`ceiling(per_sample x samples)`, the one place the arithmetic lives.

    Every operation runs in `money.arithmetic_context()` (prec 40), **never** the ambient decimal
    context (R2-B2): `per_sample * samples` used the caller's context, so a process whose
    context carried a small precision or `ROUND_DOWN` rounded the worst case *down* and
    the guard authorized a budget below what the run could spend. Nothing in `infrx/`
    changes the ambient context today, which is exactly why it was invisible.

    Raises `BudgetExceeded` rather than letting `decimal.InvalidOperation` out: a number
    too large to quantize is a refusal, not a 500.
    """
    if not isinstance(ceilings, TokenCeilings):
        raise errors.BudgetExceeded("a worst case needs validated token ceilings")
    try:
        per_sample = money.maximum_hold(ceilings.input_tokens, ceilings.billed_output_tokens,
                                        rate.input_per_million, rate.output_per_million)
        product = money.arithmetic_context().multiply(per_sample, Decimal(samples))
        # `money.parse` enforces the `numeric(20, 8)` domain: `ceiling` alone quantizes
        # happily past 10^12, so a large enough sample count produced a reservation no
        # ledger column could hold.
        return money.parse(money.ceiling(product))
    except (decimal.DecimalException, ValueError) as exc:
        raise errors.BudgetExceeded(
            f"the worst case is not a representable amount: {exc}") from exc


def per_sample_cost(rate: ProviderRate, ceilings: TokenCeilings) -> Decimal:
    return worst_case(rate, ceilings, 1)


def estimate_worst_case(rates: RateTable, *, model: str, samples: int,
                        ceilings: TokenCeilings, at: datetime) -> CostEstimate:
    """The reservation a live run would need, as a report. Unpriced models answer, they
    do not raise: a dry run's job is to report what it would cost *and* that it cannot."""
    samples = _sample_count(samples)
    rate = rates.rate_for(model, at)
    if rate is None:
        return CostEstimate(model=model, samples=samples, ceilings=ceilings, note=UNPRICED_NOTE)
    return CostEstimate(model=model, samples=samples, ceilings=ceilings,
                        price_version=rate.price_version,
                        per_sample=per_sample_cost(rate, ceilings),
                        worst_case_total=worst_case(rate, ceilings, samples),
                        note=f"worst case at {rate.price_version} ({rate.source})")


def require_live_submission(settings: PilotSettings, estimate: CostEstimate, *,
                            rates: RateTable, at: datetime) -> Decimal:
    """R57: the guard before any provider client is constructed.

    Returns the **recomputed** worst-case total a submission may reserve, or raises a
    typed `DomainError`. Every condition is checked here rather than trusted from the
    estimate, so `dry_run`, a zero budget, an unknown mode, a missing or withdrawn rate,
    a stale estimate and an oversized total each refuse.
    """
    mode = getattr(settings, "judge_mode", None)
    if mode != JUDGE_MODE_LIVE:
        raise errors.BudgetExceeded(f"judge mode {mode!r} is not {JUDGE_MODE_LIVE!r} "
                                    "and cannot authorize a live submission")
    budget = _positive_money(getattr(settings, "judge_live_budget_usd", None))
    if budget is None:
        raise errors.BudgetExceeded("a live submission needs a positive JUDGE_LIVE_BUDGET_USD")
    samples = estimate.samples
    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise errors.BudgetExceeded("a live submission needs at least one sample")
    if not estimate.priced:
        # 02: "Pricing estimates without a hard maximum cannot authorize live
        # submission." A half-filled report - a per-sample amount with no total, or a
        # total that is not a positive amount - is exactly that, and it is also what an
        # operator would have approved without seeing a number.
        raise errors.BudgetExceeded("an unpriced estimate cannot authorize a live submission")
    rate = rates.rate_for(estimate.model, at)
    if rate is None:
        raise errors.BudgetExceeded(
            f"model {estimate.model} has no approved rate effective at {at.isoformat()}: "
            "an unpriced estimate cannot authorize a live submission")
    if estimate.price_version is not None and estimate.price_version != rate.price_version:
        raise errors.BudgetExceeded(
            f"the estimate was priced at {estimate.price_version} but {rate.price_version} "
            "is now effective: re-estimate before submitting")
    total = worst_case(rate, estimate.ceilings, samples)
    if _positive_money(total) is None:
        raise errors.BudgetExceeded("the worst case is not a finite positive amount")
    if estimate.worst_case_total is not None and estimate.worst_case_total != total:
        # A report that disagrees with the recomputation is stale (or forged). Either
        # way the number an operator approved is not the number we would spend.
        raise errors.BudgetExceeded(f"the estimate says {estimate.worst_case_total} but the "
                                    f"worst case is {total}: re-estimate before submitting")
    if total > budget:
        raise errors.BudgetExceeded(f"the worst case {total} exceeds the live budget {budget}")
    return total


def live_submission_allowed(settings: PilotSettings, estimate: CostEstimate, *,
                            rates: RateTable, at: datetime) -> bool:
    """`require_live_submission` as a predicate, for a report that must not raise.

    It catches **everything**, deliberately. A settings object missing `judge_mode`, a
    `ceilings` of `None`, a rate table that misbehaves - all of it answers "not allowed",
    which is the safe direction for a money decision and the only honest answer for a
    function documented not to raise. `require_live_submission` still raises typed errors
    for the caller that needs to know why.
    """
    try:
        require_live_submission(settings, estimate, rates=rates, at=at)
    except Exception:                                  # noqa: BLE001 - fail closed
        return False
    return True
