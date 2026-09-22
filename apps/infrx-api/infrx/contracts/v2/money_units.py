"""Three denominations, one arithmetic (F2P item 2).

`contracts/money.py` stays the only money arithmetic in the tree: exact
`Decimal`, `numeric(20, 8)` domain, one rounding per debit, no binary float. This
module adds the thing v1 did not have — a **unit** — as three distinct runtime
types:

* `Credit` — the consumer product unit. Not dollars (`research/platforms/02-credits.md`).
* `Usd` — the historical `credit_ledger.delta_usd` / `usage_events.cost_usd` regime.
  Preserved, never relabelled.
* `ProviderUsd` — external provider/teacher/judge budgets, which are dollars but
  not the customer's dollars and are never summed with `Usd`.

The three are **siblings, not aliases and not subclasses of each other**, so no
`isinstance` check can be satisfied by the wrong unit, and every binary operation
returns `NotImplemented` for a different unit — which is how
`Credit("1") + Usd("1")` becomes a `TypeError` from the interpreter rather than a
silently plausible number.

**There is no conversion function, here or anywhere else in the tree.** The plan
fixes no exchange rate (`02-credits.md` §"Existing USD records"), so a function
that produced one would be inventing product policy. The only way out of an
amount is `raw(unit)`, which refuses to answer unless the caller names the unit
it already expects; `Credit(Usd("1"))` raises, because `money.parse` accepts only
a string, an int or a `Decimal`.

An amount is always **already rounded** to 1e-8: `money.parse` refuses anything
finer. An unrounded intermediate (a per-token cost) therefore cannot be held in
one of these types at all, which is what keeps "rounded once, at the end" a
property of the types instead of a rule somebody has to remember.
"""
from __future__ import annotations

from decimal import Decimal
from typing import ClassVar, Iterable, TypeVar

from .. import money

# The unit tokens as they cross JSON and as the database stores them. Frozen.
CREDIT = "CREDIT"
USD = "USD"
PROVIDER_USD = "PROVIDER_USD"
UNITS: tuple[str, ...] = (CREDIT, USD, PROVIDER_USD)

# The accounting regime a usage/admission row declares (06-database-map.md,
# `usage_events`). A row's regime fixes its unit; the pair is never inferred from
# the magnitude of an amount.
LEGACY_USD_REGIME = "legacy_usd"
CREDIT_REGIME = "credit"
ACCOUNTING_REGIMES: tuple[str, ...] = (LEGACY_USD_REGIME, CREDIT_REGIME)
REGIME_UNITS: dict[str, str] = {LEGACY_USD_REGIME: USD, CREDIT_REGIME: CREDIT}


class Amount:
    """Shared arithmetic; abstract. A unit is a subclass, never a parameter."""

    __slots__ = ("_value",)
    UNIT: ClassVar[str] = ""

    def __init__(self, value: str | int | Decimal) -> None:
        if not type(self).UNIT:
            raise TypeError("Amount is abstract: use Credit, Usd or ProviderUsd")
        # `money.parse` is the trust boundary: it refuses floats, bools, NaN,
        # exponents, '+', leading zeros, negative zero, more than eight
        # fractional digits and anything outside numeric(20, 8) — and it refuses
        # another `Amount`, which is what makes a cross-unit cast impossible.
        object.__setattr__(self, "_value", money.parse(value))

    # Immutability: an amount that could be edited in place would let a caller
    # move money after the record holding it was validated.
    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def raw(self, unit: str) -> Decimal:
        """The exact `Decimal`, but only for a caller that names the right unit.

        The one way out of the type, and deliberately noisy: `credit.raw(USD)`
        raises instead of handing over a number that would then be added to
        dollars.
        """
        if unit != type(self).UNIT:
            raise TypeError(f"{type(self).__name__} holds {type(self).UNIT}, not {unit}")
        return self._value

    def __str__(self) -> str:
        """The JSON form: fixed point, exactly eight fractional digits."""
        return money.format_money(self._value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self}')"

    def _peer(self, other: object) -> Decimal | None:
        """`other`'s value when it is the very same unit, else None (-> NotImplemented)."""
        return other._value if type(other) is type(self) else None

    def __eq__(self, other: object) -> bool:
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return self._value == peer

    def __hash__(self) -> int:
        return hash((type(self).UNIT, self._value))

    def __lt__(self, other: object) -> bool:
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return self._value < peer

    def __le__(self, other: object) -> bool:
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return self._value <= peer

    def __gt__(self, other: object) -> bool:
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return self._value > peer

    def __ge__(self, other: object) -> bool:
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return self._value >= peer

    def __add__(self, other: object):
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return type(self)(money.arithmetic_context().add(self._value, peer))

    def __sub__(self, other: object):
        peer = self._peer(other)
        if peer is None:
            return NotImplemented
        return type(self)(money.arithmetic_context().subtract(self._value, peer))

    def __neg__(self):
        # `-0.00000000` is not a money value (`money.parse` refuses negative zero),
        # so negating zero answers zero rather than raising.
        if self._value == 0:
            return type(self)(money.ZERO)
        return type(self)(money.arithmetic_context().minus(self._value))

    @property
    def is_negative(self) -> bool:
        return self._value < 0

    @property
    def is_zero(self) -> bool:
        return self._value == 0

    @classmethod
    def zero(cls):
        return cls(money.ZERO)


class Credit(Amount):
    """The consumer product unit. `numeric(20, 8)` CREDIT; never displayed with `$`."""

    __slots__ = ()
    UNIT = CREDIT


class Usd(Amount):
    """The legacy regime's dollars: existing `delta_usd` / `cost_usd` history."""

    __slots__ = ()
    UNIT = USD


class ProviderUsd(Amount):
    """External provider/teacher/judge budgets. Dollars, but not a customer balance."""

    __slots__ = ()
    UNIT = PROVIDER_USD


A = TypeVar("A", bound=Amount)

UNIT_TYPES: dict[str, type[Amount]] = {CREDIT: Credit, USD: Usd, PROVIDER_USD: ProviderUsd}

ZERO_CREDIT = Credit("0")
ZERO_USD = Usd("0")
ZERO_PROVIDER_USD = ProviderUsd("0")


def total(amounts: Iterable[A], unit_type: type[A]) -> A:
    """Sum of one unit's amounts. A foreign unit in the iterable is a `TypeError`.

    Take the unit explicitly rather than from the first element, so summing an
    empty history still answers in the unit the caller asked about instead of
    guessing one — the "no mixed totals" half of CREDIT-UNITS.
    """
    if not (isinstance(unit_type, type) and issubclass(unit_type, Amount) and unit_type.UNIT):
        raise TypeError(f"{unit_type!r} is not a denomination")
    running: A = unit_type.zero()
    for amount in amounts:
        running = running + amount        # TypeError on any other unit
    return running


def unit_of(regime: str) -> str:
    """The one unit an accounting regime may carry. An unknown regime refuses."""
    try:
        return REGIME_UNITS[regime]
    except KeyError:
        raise ValueError(f"unknown accounting regime: {regime!r}") from None


def parse_amount(value: object, unit: str) -> Amount:
    """A JSON `{amount, unit}` pair as the typed amount for that unit.

    The trust boundary for a stored or transmitted amount: the unit comes from
    the payload's own explicit field and must be one of the three. A unit is
    never inferred from a field name or from a magnitude
    (`02-credits.md`: "never guess the unit from a field name").
    """
    try:
        unit_type = UNIT_TYPES[unit]
    except KeyError:
        raise ValueError(f"unknown money unit: {unit!r}") from None
    return unit_type(value)


# --- the cross-language money-unit parity table ------------------------------
# Every pair both halves must classify identically. `unit` is the unit the payload
# declares; `valid` is whether it may be read as that unit at all; `canonical` is
# the one spelling it normalises to. A case is here precisely because getting it
# wrong is a unit confusion or a rounding error rather than a typo.
PARITY_CASES: tuple[dict[str, object], ...] = tuple(sorted(
    (
        {"unit": "CREDIT", "input": "10000.00000000", "valid": True,
         "canonical": "10000.00000000"},
        {"unit": "CREDIT", "input": "10000", "valid": True, "canonical": "10000.00000000"},
        {"unit": "CREDIT", "input": "0", "valid": True, "canonical": "0.00000000"},
        {"unit": "CREDIT", "input": "-0", "valid": False, "canonical": None},
        {"unit": "CREDIT", "input": "-1.50000000", "valid": True, "canonical": "-1.50000000"},
        {"unit": "CREDIT", "input": "1e3", "valid": False, "canonical": None},
        {"unit": "CREDIT", "input": "0.000000001", "valid": False, "canonical": None},
        {"unit": "CREDIT", "input": "1000000000000.00000000", "valid": False, "canonical": None},
        {"unit": "CREDIT", "input": "999999999999.99999999", "valid": True,
         "canonical": "999999999999.99999999"},
        {"unit": "CREDIT", "input": "$10.00", "valid": False, "canonical": None},
        {"unit": "CREDIT", "input": "10 credits", "valid": False, "canonical": None},
        {"unit": "USD", "input": "4.21500000", "valid": True, "canonical": "4.21500000"},
        {"unit": "USD", "input": "-3.00000000", "valid": True, "canonical": "-3.00000000"},
        {"unit": "USD", "input": "NaN", "valid": False, "canonical": None},
        {"unit": "PROVIDER_USD", "input": "500.00000000", "valid": True,
         "canonical": "500.00000000"},
        {"unit": "PROVIDER_USD", "input": "+1.00000000", "valid": False, "canonical": None},
        {"unit": "CREDITS", "input": "1.00000000", "valid": False, "canonical": None},
        {"unit": "", "input": "1.00000000", "valid": False, "canonical": None},
    ),
    key=lambda case: (case["unit"], case["input"])))
