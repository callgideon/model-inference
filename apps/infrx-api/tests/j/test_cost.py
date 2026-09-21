#!/usr/bin/env python3
"""JUDGE-SCORES / JUDGE-BUDGET: the worst-case estimate, and the egress guard.

Also the import-hygiene proof: the dry-run path must not so much as *import* the
provider SDK, which is checked in a subprocess rather than asserted in prose.

    uv run --frozen pytest -q tests/j/test_cost.py
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from decimal import Decimal

import pytest
from infrx.contracts import errors, money
from infrx.contracts.limits import DEFAULTS
from infrx.judge import (APPROVED_RATES, DEFAULT_CEILINGS, UNPRICED_NOTE, ProviderRate,
                         StaticRateTable, TokenCeilings, estimate_worst_case,
                         live_submission_allowed, require_live_submission)

from . import fakes

LIVE = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("100"))


def estimate(rates=fakes.TEST_RATES, *, samples: int = 50, ceilings=DEFAULT_CEILINGS,
             model: str = fakes.JUDGE_MODEL):
    return estimate_worst_case(rates, model=model, samples=samples, ceilings=ceilings,
                               at=fakes.NOW)


# --- the reservation ---------------------------------------------------------------
def test_the_estimate_is_a_worst_case_at_the_versioned_rate():
    """02: reserve the maximum, at versioned provider rates, rounded up. The arithmetic
    is `money`'s ceiling formula on the ceilings, so it can never round in the
    platform's favour."""
    one = estimate(samples=1)
    assert one.priced and one.price_version == "test-rates-v1"
    expected = money.maximum_hold(DEFAULT_CEILINGS.input_tokens,
                                 DEFAULT_CEILINGS.billed_output_tokens,
                                 Decimal("5"), Decimal("25"))
    assert one.per_sample == expected
    assert one.worst_case_total == expected
    assert estimate(samples=50).worst_case_total == money.ceiling(expected * 50)
    assert estimate(samples=0).worst_case_total == money.ZERO


def test_reasoning_tokens_are_billed_as_output_and_included():
    """`06` §3.8: adaptive thinking is billed as output. A reservation that counts only
    `max_tokens` under-reserves by the whole thinking budget."""
    ceilings = TokenCeilings(input_tokens=1_000, output_tokens=1_000, reasoning_tokens=1_000)
    assert ceilings.billed_output_tokens == 2_000
    with_thinking = estimate(samples=1, ceilings=ceilings)
    without = estimate(samples=1, ceilings=TokenCeilings(input_tokens=1_000, output_tokens=1_000))
    assert with_thinking.per_sample > without.per_sample
    assert DEFAULT_CEILINGS.reasoning_tokens > 0, "the shipped ceilings include a thinking budget"


def test_the_reservation_is_never_reduced_by_a_batch_discount():
    """`06` §3.8 prices batches at x0.5. Halving a worst case turns it into an average;
    the discount belongs to reconciliation, where the actual usage is known."""
    rate = fakes.TEST_RATE
    full_price = money.maximum_hold(DEFAULT_CEILINGS.input_tokens,
                                   DEFAULT_CEILINGS.billed_output_tokens,
                                   rate.input_per_million, rate.output_per_million)
    assert estimate(samples=1).per_sample == full_price


@pytest.mark.parametrize("kw", [{"input_tokens": -1}, {"output_tokens": 0},
                                {"output_tokens": True}, {"reasoning_tokens": -5},
                                {"output_tokens": 1.5}])
def test_a_ceiling_that_would_reserve_nothing_is_refused(kw):
    """A zero output ceiling reserves nothing and then bills whatever arrives - the
    defect r1 R55 closed at admission, in the judge's currency."""
    base = {"input_tokens": 100, "output_tokens": 100}
    with pytest.raises(ValueError):
        TokenCeilings(**{**base, **kw})


@pytest.mark.parametrize("samples", [-1, True, 1.5, "5"])
def test_a_sample_count_is_a_nonnegative_integer(samples):
    with pytest.raises(ValueError):
        estimate(samples=samples)


# --- rates come from an approved source, and there is not one yet ------------------
def test_the_shipped_rate_table_is_empty_and_says_why():
    """Prices come only from `research/cross-cutting/cloud-pricing.md` (r1 R45's rule,
    and this package's). That document prices GPU capacity and carries **no per-MTok
    row** for any judge model, so the approved table is empty, the estimate is
    unpriced, and the note carries the TO BE VERIFIED marker rather than a guess."""
    assert APPROVED_RATES.rows == ()
    unpriced = estimate(APPROVED_RATES)
    assert unpriced.priced is False
    assert unpriced.per_sample is None and unpriced.worst_case_total is None
    assert unpriced.price_version is None
    assert unpriced.note == UNPRICED_NOTE and "TO BE VERIFIED" in unpriced.note
    assert "cloud-pricing.md" in unpriced.note


def test_an_unknown_model_is_unpriced_rather_than_free():
    assert estimate(model="claude-sonnet-5").priced is False


@pytest.mark.parametrize("kw", [{"input_per_million": "-1"}, {"output_per_million": "-0.5"},
                                {"input_per_million": 1.5}, {"input_per_million": "1e3"},
                                {"input_per_million": "NaN"}, {"price_version": ""},
                                {"model": ""}, {"source": ""}])
def test_a_rate_row_is_validated_at_the_boundary(kw):
    """A negative rate turns output into a credit; a float is not money; a row with no
    source is a price nobody approved."""
    good = {"price_version": "v", "model": "m", "input_per_million": Decimal("5"),
            "output_per_million": Decimal("25"), "source": "doc"}
    with pytest.raises(ValueError):
        ProviderRate(**{**good, **kw})


def test_a_rate_row_normalizes_to_the_money_scale():
    row = ProviderRate(price_version="v", model="m", input_per_million="5",
                       output_per_million="25", source="doc")
    assert row.input_per_million == money.parse("5")
    assert StaticRateTable((row,)).rate_for("m", fakes.NOW) is row
    assert StaticRateTable((row,)).rate_for("other", fakes.NOW) is None


# --- the guard ---------------------------------------------------------------------
def test_the_defaults_can_never_authorize_a_submission():
    """08 §5: `JUDGE_MODE=dry_run`, `JUDGE_LIVE_BUDGET_USD=0`. Both refuse on their
    own, so flipping one setting is not enough to spend money."""
    priced = estimate()
    # The message matters: each condition must refuse on its *own* account, or removing
    # one of the three guards would still look refused because another one fired.
    with pytest.raises(errors.BudgetExceeded, match="judge mode"):
        require_live_submission(DEFAULTS, priced)
    assert live_submission_allowed(DEFAULTS, priced) is False
    # live mode with the default zero budget
    with pytest.raises(errors.BudgetExceeded, match="JUDGE_LIVE_BUDGET_USD"):
        require_live_submission(DEFAULTS.replace(judge_mode="live"), priced)
    # a budget without live mode
    with pytest.raises(errors.BudgetExceeded, match="judge mode"):
        require_live_submission(DEFAULTS.replace(judge_live_budget_usd=Decimal("100")), priced)
    # both, and it is allowed
    assert live_submission_allowed(LIVE, priced) is True


def test_a_pricing_estimate_without_a_hard_maximum_cannot_authorize_a_submission():
    """02, verbatim. An unpriced estimate is exactly that case: there is no maximum to
    reserve, so live submission stays disabled even in live mode with a budget."""
    unpriced = estimate(APPROVED_RATES)
    with pytest.raises(errors.BudgetExceeded, match="unpriced"):
        require_live_submission(LIVE, unpriced)
    assert live_submission_allowed(LIVE, unpriced) is False


def test_a_worst_case_over_the_budget_is_refused():
    small = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("0.01"))
    assert live_submission_allowed(small, estimate(samples=50)) is False
    assert live_submission_allowed(LIVE, estimate(samples=50)) is True


# --- import hygiene: no provider SDK on the dry-run path ---------------------------
PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "infrx"


def test_the_judge_package_never_imports_the_provider_sdk():
    """A guard alone is not enough: an import at module scope builds no client but it
    does load the SDK into every process that touches the judge, and the next
    refactor puts a client next to it. The source is the check."""
    sdk_import = re.compile(r"^\s*(?:import|from)\s+anthropic\b", re.MULTILINE)
    offenders = [path.name for path in sorted((PACKAGE / "judge").rglob("*.py"))
                 if sdk_import.search(path.read_text())]
    assert offenders == []
    # and the check is not vacuous: it fires on the line it exists to forbid
    assert sdk_import.search("from anthropic import Anthropic") is not None
    assert sdk_import.search("    import anthropic  # deferred") is not None


def test_importing_the_dry_run_path_loads_no_provider_sdk():
    """Checked in a fresh interpreter, because another test in the same session could
    have imported the SDK for its own reasons and hidden this."""
    script = ("import sys, asyncio\n"
              "import infrx.judge as judge\n"
              "from infrx.judge import plan_dry_run, require_live_submission\n"
              "assert 'anthropic' not in sys.modules, sorted(m for m in sys.modules "
              "if 'anthropic' in m)\n"
              "print('clean')\n")
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          cwd=PACKAGE.parent, env={"PYTHONPATH": str(PACKAGE.parent),
                                                   "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "clean"


def test_the_provider_sdk_is_importable_so_the_check_means_something():
    """If `anthropic` were simply absent the two cases above would pass for the wrong
    reason. It is a pinned `judge` extra and `make api-env` installs it."""
    done = subprocess.run([sys.executable, "-c", "import anthropic"], capture_output=True,
                          text=True, env={"PATH": "/usr/bin:/bin"})
    if done.returncode != 0:
        pytest.skip("the judge extra is not installed in this environment "
                    "(run make api-env); the no-import checks are then vacuous")
