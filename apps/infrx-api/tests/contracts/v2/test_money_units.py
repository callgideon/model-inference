#!/usr/bin/env python3
"""CREDIT-UNITS: three denominations, one arithmetic, no conversion (F2P item 2).

    uv run --frozen pytest -q tests/contracts/v2/test_money_units.py
"""
from __future__ import annotations

import decimal
import inspect
from decimal import Decimal

import pytest
from infrx.contracts import money
from infrx.contracts.v2 import money_units as mu

UNIT_TYPES = (mu.Credit, mu.Usd, mu.ProviderUsd)


def test_the_three_units_are_distinct_types_not_aliases():
    """A unit is a class, so no `isinstance` check can be satisfied by the wrong
    one. Aliasing them (the thing 02-credits.md calls a cosmetic rename) would make
    every guard below vacuous."""
    assert len({mu.Credit, mu.Usd, mu.ProviderUsd}) == 3
    for unit_type in UNIT_TYPES:
        assert issubclass(unit_type, mu.Amount)
        others = [other for other in UNIT_TYPES if other is not unit_type]
        assert not any(issubclass(unit_type, other) for other in others)
        assert not isinstance(unit_type("1"), tuple(others))
    assert {t.UNIT for t in UNIT_TYPES} == set(mu.UNITS) == {"CREDIT", "USD", "PROVIDER_USD"}


def test_the_abstract_base_cannot_be_instantiated():
    with pytest.raises(TypeError, match="abstract"):
        mu.Amount("1.00000000")


@pytest.mark.parametrize("unit_type", UNIT_TYPES, ids=[t.UNIT for t in UNIT_TYPES])
def test_mixed_unit_arithmetic_is_refused(unit_type):
    """The invariant: `+`, `-` and every ordering across two units raise `TypeError`,
    because `_peer` answers None and the operator returns NotImplemented."""
    mine = unit_type("5.00000000")
    for other_type in (t for t in UNIT_TYPES if t is not unit_type):
        theirs = other_type("5.00000000")
        for operation in (lambda a, b: a + b, lambda a, b: a - b, lambda a, b: a < b,
                          lambda a, b: a <= b, lambda a, b: a > b, lambda a, b: a >= b):
            with pytest.raises(TypeError):
                operation(mine, theirs)
        # equality is False rather than an error: `==` must never raise, but it must
        # also never call two units equal.
        assert (mine == theirs) is False
        assert (mine != theirs) is True
        assert hash(mine) != hash(theirs)


def test_same_unit_arithmetic_is_exact_and_shares_the_v1_money_path():
    a, b = mu.Credit("10000.00000000"), mu.Credit("0.00000001")
    assert str(a - b) == "9999.99999999"
    assert str(a + b) == "10000.00000001"
    assert str(-mu.Credit("1.25")) == "-1.25000000"
    assert str(-mu.Credit("0")) == "0.00000000", "negative zero is not a money value"
    assert mu.Credit("1") == mu.Credit("1.00000000")
    assert mu.total([], mu.Credit) == mu.ZERO_CREDIT
    assert str(mu.total([mu.Credit("1.5"), mu.Credit("2.25")], mu.Credit)) == "3.75000000"


def test_a_total_refuses_a_foreign_unit_in_the_iterable():
    """The "no mixed totals" half of CREDIT-UNITS: one wrong element fails the sum
    rather than being coerced or skipped."""
    with pytest.raises(TypeError):
        mu.total([mu.Credit("1"), mu.Usd("1")], mu.Credit)
    with pytest.raises(TypeError):
        mu.total([], str)


def test_conversion_is_impossible_by_construction():
    """No function converts one unit into another, and none can be improvised: a
    constructor refuses another `Amount`, and `raw` refuses a foreign unit name."""
    for target in UNIT_TYPES:
        for source in (t("1.00000000") for t in UNIT_TYPES if t is not target):
            with pytest.raises(ValueError, match="must be a string, int or Decimal"):
                target(source)
    credit = mu.Credit("1.00000000")
    assert credit.raw(mu.CREDIT) == Decimal("1.00000000")
    for wrong in (mu.USD, mu.PROVIDER_USD, "credit", ""):
        with pytest.raises(TypeError, match="holds CREDIT"):
            credit.raw(wrong)
    # and nothing in the module is named like a converter
    forbidden = ("convert", "exchange", "to_usd", "to_credit", "in_usd", "in_credit", "rate_to")
    surface = [name for name in dir(mu) if not name.startswith("_")]
    surface += [f"{t.__name__}.{name}" for t in UNIT_TYPES for name in dir(t)
                if not name.startswith("_")]
    assert [name for name in surface if any(bad in name.lower() for bad in forbidden)] == []


@pytest.mark.parametrize("bad", [1.5, True, float("nan"), float("inf"), None, b"1",
                                 Decimal("NaN"), "1e3", "+1", "01", "-0", " 1", "1 ",
                                 "$1", "1.000000001", "1000000000000"])
def test_out_of_domain_and_wrong_typed_inputs_are_refused(bad):
    """The trust boundary is `money.parse`, so floats, bools, NaN, exponents, signs,
    leading zeros, negative zero, whitespace, sub-1e-8 precision and anything
    outside numeric(20, 8) are refused for every unit."""
    for unit_type in UNIT_TYPES:
        with pytest.raises((ValueError, decimal.InvalidOperation)):
            unit_type(bad)


def test_the_domain_edges_are_the_v1_money_domain():
    assert str(mu.Credit("999999999999.99999999")) == "999999999999.99999999"
    with pytest.raises(ValueError):
        mu.Credit("1000000000000.00000000")
    with pytest.raises(ValueError):
        mu.Credit("999999999999.99999999") + mu.Credit("0.00000001")
    assert money.MAX_VALUE == Decimal("1000000000000")


def test_an_amount_is_immutable():
    credit = mu.Credit("1.00000000")
    with pytest.raises(AttributeError):
        credit._value = Decimal("999")
    with pytest.raises(AttributeError):
        credit.amount = "999"
    with pytest.raises(AttributeError):
        del credit._value
    assert str(credit) == "1.00000000"


def test_the_regime_fixes_the_unit_and_an_unknown_one_refuses():
    assert mu.unit_of("credit") == mu.CREDIT
    assert mu.unit_of("legacy_usd") == mu.USD
    assert set(mu.ACCOUNTING_REGIMES) == {"credit", "legacy_usd"}
    for unknown in ("CREDIT", "usd", "", "provider_usd"):
        with pytest.raises(ValueError, match="unknown accounting regime"):
            mu.unit_of(unknown)


def test_parse_amount_takes_the_unit_from_the_payload():
    assert isinstance(mu.parse_amount("1.00000000", mu.CREDIT), mu.Credit)
    assert isinstance(mu.parse_amount("1.00000000", mu.USD), mu.Usd)
    assert isinstance(mu.parse_amount("1.00000000", mu.PROVIDER_USD), mu.ProviderUsd)
    for unknown in ("CREDITS", "usd", "", None, 1):
        with pytest.raises(ValueError, match="unknown money unit"):
            mu.parse_amount("1.00000000", unknown)


def test_the_three_units_expose_exactly_the_same_api():
    """A unit with an extra method would be a place for a conversion to hide, and a
    unit missing one would push a caller into `raw`."""
    surfaces = {t.UNIT: sorted(name for name in dir(t) if not name.startswith("_"))
                for t in UNIT_TYPES}
    assert len({tuple(names) for names in surfaces.values()}) == 1, surfaces
    assert surfaces["CREDIT"] == ["UNIT", "is_negative", "is_zero", "raw", "zero"]


def test_zero_constants_and_predicates():
    assert (mu.ZERO_CREDIT.is_zero, mu.ZERO_USD.is_zero, mu.ZERO_PROVIDER_USD.is_zero) == \
        (True, True, True)
    assert mu.Credit("-0.00000001").is_negative is True
    assert mu.Credit("0.00000001").is_negative is False
    assert mu.Credit.zero() == mu.ZERO_CREDIT


def test_arithmetic_is_decimal_and_no_signature_takes_a_float():
    """`money.py` stays the only arithmetic; no unit method invites a binary float."""
    assert inspect.signature(mu.Amount.__init__).parameters["value"].annotation == \
        "str | int | Decimal"
    assert isinstance(mu.Credit("1").raw(mu.CREDIT), Decimal)
