#!/usr/bin/env python3
"""F-CONTRACT: money. The fixture table is the contract; the property checks are
a seeded sweep over it, so a rounding change cannot pass unnoticed.

    uv run --frozen pytest -q tests/contracts/test_money.py
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest
from infrx.contracts import fixtures, money

CASES = fixtures.load("money_cases.json")
SEED = 20260920


@pytest.mark.parametrize("case", CASES["debits"], ids=lambda c: c["name"])
def test_debit_fixture(case):
    debit = money.debit(case["prompt_tokens"], case["completion_tokens"],
                        Decimal(case["input_rate"]), Decimal(case["output_rate"]))
    assert money.format_money(debit) == case["expected"]


@pytest.mark.parametrize("case", CASES["holds"], ids=lambda c: c["name"])
def test_hold_fixture(case):
    hold = money.maximum_hold(case["max_input_tokens"], case["max_output_tokens"],
                              Decimal(case["input_rate"]), Decimal(case["output_rate"]))
    assert money.format_money(hold) == case["expected"]


@pytest.mark.parametrize("case", CASES["ledger_deltas"], ids=lambda c: c["name"])
def test_ledger_delta_fixture(case):
    assert money.format_money(money.parse(case["amount"])) == case["amount"]


@pytest.mark.parametrize("value", CASES["rejected"], ids=lambda v: repr(v)[:24])
def test_rejected_money_values(value):
    with pytest.raises(ValueError):
        money.parse(value)


def test_ceiling_diverges_from_half_up_on_the_same_inputs():
    """The documented divergence: a hold never rounds in the customer's favour."""
    debit = money.debit(1, 0, Decimal("0.00400000"), Decimal("0.60000000"))
    hold = money.maximum_hold(1, 0, Decimal("0.00400000"), Decimal("0.60000000"))
    assert money.format_money(debit) == "0.00000000"
    assert money.format_money(hold) == "0.00000001"
    assert hold > debit


def test_format_never_emits_negative_zero():
    assert money.format_money(Decimal("-0.000000001") * 0) == "0.00000000"
    assert money.format_money(Decimal("0")) == "0.00000000"


def test_floats_are_never_money():
    for value in (0.1, 1.0, -2.5, True):
        with pytest.raises(ValueError):
            money.parse(value)


def test_available_balance_is_total_minus_reserved():
    assert money.available(Decimal("25.00000000"), Decimal("0.00737280")) == Decimal("24.99262720")


# --- seeded property sweep ---------------------------------------------------
def _rate(rng):
    return money.parse(f"{rng.randrange(0, 10_000_000)}.{rng.randrange(0, 100_000_000):08d}")


def test_property_debit_is_scaled_stable_and_monotonic():
    """Seed 20260920: 500 random (tokens, rates) triples."""
    rng = random.Random(SEED)
    for _ in range(500):
        prompt, completion = rng.randrange(0, 40_000), rng.randrange(0, 4_096)
        input_rate, output_rate = _rate(rng), _rate(rng)
        debit = money.debit(prompt, completion, input_rate, output_rate)
        exact = money.cost(prompt, completion, input_rate, output_rate)

        # exactly eight fractional digits, and the string round-trips
        text = money.format_money(debit)
        assert len(text.split(".")[1]) == 8 and "e" not in text.lower()
        assert money.parse(text) == debit
        # one rounding, half up, at most half a unit away from the exact cost
        assert abs(exact - debit) <= money.SCALE / 2
        # the worst-case hold on the same ceilings is never smaller than the debit
        hold = money.maximum_hold(prompt, completion, input_rate, output_rate)
        assert hold >= debit and hold - exact < money.SCALE
        # and never negative
        assert debit >= 0


def test_property_half_up_boundary_always_rounds_away_from_zero():
    """Construct raw costs ending in exactly ...5 at the ninth digit."""
    rng = random.Random(SEED + 1)
    for _ in range(200):
        # prompt=1 -> cost = rate / 1e6, so a rate with three decimals ending in 5
        # puts a 5 exactly at the ninth fractional digit of the cost.
        rate = money.parse(f"{rng.randrange(0, 1000)}.{rng.randrange(0, 100):02d}5")
        exact = money.cost(1, 0, rate, money.ZERO)
        debit = money.debit(1, 0, rate, money.ZERO)
        assert debit == money.ceiling(exact), (rate, exact, debit)


def test_property_token_counts_must_be_nonnegative_integers():
    for bad in (-1, 1.0, "5", None, True):
        with pytest.raises(ValueError):
            money.debit(bad, 0, Decimal("0.2"), Decimal("0.6"))
