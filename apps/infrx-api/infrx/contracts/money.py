"""USD money: `Decimal` only, scale 1e-8, one rounding per debit.

Never `float`, never binary arithmetic. `parse` is a trust boundary: it accepts
the plain decimal strings JSON carries and rejects everything a caller could use
to smuggle in a float, an exponent, a NaN or a negative zero.
"""
from __future__ import annotations

import decimal
import re
from decimal import Decimal

CONTEXT = decimal.Context(prec=40)      # >= 40 per contracts v1
SCALE = Decimal("0.00000001")           # numeric(20, 8) USD
FRACTIONAL_DIGITS = 8
PER_MILLION = Decimal(1_000_000)
MAX_DIGITS = 20                         # numeric(20, 8): 12 integral + 8 fractional

# No exponent, no leading '+', no leading zeros, at most eight fractional digits.
# Matched with `fullmatch`: `$` alone would also accept a trailing newline.
_PLAIN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?")

ZERO = Decimal("0.00000000")


def parse(raw: object) -> Decimal:
    """A JSON money string (or an int/Decimal already in hand) as an exact Decimal."""
    if isinstance(raw, bool) or isinstance(raw, float):
        raise ValueError(f"money must not be a {type(raw).__name__}: {raw!r}")
    if isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, Decimal):
        if not raw.is_finite():
            raise ValueError(f"money must be finite: {raw!r}")
        value = raw
    elif isinstance(raw, str):
        if not _PLAIN.fullmatch(raw):
            raise ValueError(
                "money must be a plain decimal string with at most eight fractional "
                f"digits (no exponent, NaN, '+' or leading zeros): {raw!r}"
            )
        value = Decimal(raw)
        if value == 0 and raw.startswith("-"):
            raise ValueError(f"negative zero is not a money value: {raw!r}")
    else:
        raise ValueError(f"money must be a string, int or Decimal, not {type(raw).__name__}")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -FRACTIONAL_DIGITS:
        # Finer than 1e-8 is rejected, never silently rounded into the ledger.
        raise ValueError(f"money is finer than 1e-8 and would need rounding: {raw!r}")
    try:
        quantized = value.quantize(SCALE, rounding=decimal.ROUND_HALF_UP, context=CONTEXT)
    except decimal.DecimalException as exc:
        raise ValueError(f"money is not representable as numeric(20, 8): {raw!r}") from exc
    if len(quantized.as_tuple().digits) > MAX_DIGITS and quantized != 0:
        raise ValueError(f"money exceeds numeric(20, 8): {raw!r}")
    return quantized


def format_money(value: object) -> str:
    """The JSON form: fixed point, exactly eight fractional digits, no '-0'."""
    quantized = _q(value if isinstance(value, Decimal) else parse(value), decimal.ROUND_HALF_UP)
    if quantized == 0:
        quantized = quantized.copy_abs()
    return f"{quantized:f}"


def _q(value: Decimal, rounding: str) -> Decimal:
    return value.quantize(SCALE, rounding=rounding, context=CONTEXT)


def half_up(value: Decimal) -> Decimal:
    """The single rounding a final customer debit gets."""
    return _q(value, decimal.ROUND_HALF_UP)


def ceiling(value: Decimal) -> Decimal:
    """Reservations round away from the customer's favour."""
    return _q(value, decimal.ROUND_CEILING)


def _tokens(count: object, name: str) -> Decimal:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"{name} must be a nonnegative integer, not {count!r}")
    return Decimal(count)


def cost(prompt_tokens: int, completion_tokens: int, input_rate: Decimal, output_rate: Decimal) -> Decimal:
    """Unrounded (prompt x input + completion x output) / 1e6. Exact: dividing a
    finite decimal by a power of ten only shifts the exponent."""
    prompt = _tokens(prompt_tokens, "prompt_tokens")
    completion = _tokens(completion_tokens, "completion_tokens")
    total = CONTEXT.add(CONTEXT.multiply(prompt, input_rate), CONTEXT.multiply(completion, output_rate))
    return CONTEXT.divide(total, PER_MILLION)


def debit(prompt_tokens: int, completion_tokens: int, input_rate: Decimal, output_rate: Decimal) -> Decimal:
    """Final request debit: computed once, rounded once, half up."""
    return half_up(cost(prompt_tokens, completion_tokens, input_rate, output_rate))


def maximum_hold(max_input_tokens: int, max_output_tokens: int, input_rate: Decimal, output_rate: Decimal) -> Decimal:
    """Worst-case reservation on the validated ceilings: rounds up, never down."""
    return ceiling(cost(max_input_tokens, max_output_tokens, input_rate, output_rate))


def available(ledger_total: Decimal, reserved_total: Decimal) -> Decimal:
    """The balance view's third column; may be zero but reservations never exceed it."""
    return CONTEXT.subtract(ledger_total, reserved_total)
