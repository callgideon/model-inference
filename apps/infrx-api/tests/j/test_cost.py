#!/usr/bin/env python3
"""JUDGE-SCORES / JUDGE-BUDGET: the worst-case estimate, and the egress guard (R57).

Also the import-hygiene proof: the dry-run path must not so much as *import* the provider
SDK, which is checked in a subprocess rather than asserted in prose.

    uv run --frozen pytest -q tests/j/test_cost.py
"""
from __future__ import annotations

import ast
import decimal
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from infrx.contracts import errors, money
from infrx.contracts.limits import DEFAULTS
from infrx.judge import (APPROVED_RATES, DEFAULT_CEILINGS, UNPRICED_NOTE, CostEstimate,
                         ProviderRate, StaticRateTable, TokenCeilings, estimate_worst_case,
                         live_submission_allowed, per_sample_cost, require_live_submission,
                         worst_case)

from . import fakes

LIVE = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("100"))
RATE_KW = {"price_version": "v", "model": "m", "input_per_million": Decimal("5"),
           "output_per_million": Decimal("25"), "source": "doc",
           "effective_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}


def estimate(rates=fakes.TEST_RATES, *, samples: int = 50, ceilings=DEFAULT_CEILINGS,
             model: str = fakes.JUDGE_MODEL, at=None):
    return estimate_worst_case(rates, model=model, samples=samples, ceilings=ceilings,
                               at=at or fakes.NOW)


def allowed(settings=LIVE, est=None, *, rates=fakes.TEST_RATES, at=None):
    return live_submission_allowed(settings, est if est is not None else estimate(),
                                   rates=rates, at=at or fakes.NOW)


# --- the reservation ---------------------------------------------------------------
def test_the_estimate_is_a_worst_case_at_the_versioned_rate():
    """02: reserve the maximum, at versioned provider rates, rounded up. The arithmetic is
    `money`'s ceiling formula on the ceilings, so it can never round in the platform's
    favour."""
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
    """`06` §3.8 prices batches at x0.5. Halving a worst case turns it into an average; the
    discount belongs to reconciliation, where the actual usage is known."""
    rate = fakes.TEST_RATE
    full_price = money.maximum_hold(DEFAULT_CEILINGS.input_tokens,
                                    DEFAULT_CEILINGS.billed_output_tokens,
                                    rate.input_per_million, rate.output_per_million)
    assert estimate(samples=1).per_sample == full_price


@pytest.mark.parametrize("kw", [{"input_tokens": -1}, {"output_tokens": 0},
                                {"output_tokens": True}, {"reasoning_tokens": -5},
                                {"output_tokens": 1.5}])
def test_a_ceiling_that_would_reserve_nothing_is_refused(kw):
    """A zero or negative output ceiling, a boolean and a float are refused - the r1 R55
    defect (a zero ceiling makes a zero hold and then bills whatever arrives) in the
    judge's currency."""
    base = {"input_tokens": 100, "output_tokens": 100}
    with pytest.raises(ValueError):
        TokenCeilings(**{**base, **kw})


@pytest.mark.parametrize("samples", [-1, True, 1.5, "5"])
def test_a_sample_count_is_a_nonnegative_integer(samples):
    with pytest.raises(ValueError):
        estimate(samples=samples)


def test_an_unrepresentable_worst_case_refuses_rather_than_raising_arithmetic():
    """`money` is `numeric(20, 8)`, so a large enough total cannot be quantized. That is a
    refusal with a typed error, not a `decimal.InvalidOperation` out of a money path."""
    with pytest.raises(errors.BudgetExceeded):
        worst_case(fakes.TEST_RATE, DEFAULT_CEILINGS, 10 ** 30)
    # and ceilings that are not ceilings are a refusal too, not an AttributeError
    with pytest.raises(errors.BudgetExceeded):
        worst_case(fakes.TEST_RATE, None, 1)


@pytest.mark.parametrize("prec,rounding", [(3, decimal.ROUND_HALF_EVEN), (3, decimal.ROUND_DOWN),
                                           (1, decimal.ROUND_FLOOR), (5, decimal.ROUND_DOWN)])
def test_the_worst_case_ignores_the_ambient_decimal_context(prec, rounding):
    """R2-B2: `per_sample * samples` ran in the **ambient** context, so a process whose
    context carried a small precision or `ROUND_DOWN` rounded the worst case *down* - and
    the guard then authorized a budget below what the run could spend. The review's repro:
    49 samples at the test rates is exactly 5.56346000, and under `prec = 3` the estimate
    read 5.56000000 and a 5.56 budget was authorized."""
    exact = Decimal("5.56346000")
    assert estimate(samples=49).worst_case_total == exact
    below = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("5.56"))
    covering = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=exact)
    with decimal.localcontext() as ctx:
        ctx.prec, ctx.rounding = prec, rounding
        hostile = estimate(samples=49)
        assert hostile.worst_case_total == exact, "the ambient context moved the worst case"
        assert allowed(below, hostile) is False
        with pytest.raises(errors.BudgetExceeded, match="exceeds the live budget"):
            require_live_submission(below, hostile, rates=fakes.TEST_RATES, at=fakes.NOW)
        # and a budget that really does cover it still authorizes, so the fix is not just
        # "refuse everything under a strange context"
        assert require_live_submission(covering, hostile, rates=fakes.TEST_RATES,
                                       at=fakes.NOW) == exact


def test_no_decimal_arithmetic_in_the_judge_escapes_the_explicit_context():
    """The audit R2-B2 asked for, as a check rather than a claim: no bare arithmetic
    operator between money values anywhere in `infrx/judge`. Every amount goes through
    `money`, which works in `money.CONTEXT` (prec 40) throughout."""
    money_names = {"per_sample", "budget", "total", "amount", "input_per_million",
                   "output_per_million", "worst_case_total", "reserved_cost"}

    def named(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    bare = []
    for path in sorted((PACKAGE / "judge").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            if not isinstance(node.op, (ast.Mult, ast.Div, ast.Add, ast.Sub, ast.Pow)):
                continue
            touched = {named(node.left), named(node.right)} & money_names
            if touched:
                bare.append(f"{path.name}:{node.lineno}: {sorted(touched)}")
    assert bare == [], "Decimal arithmetic outside money.CONTEXT"


# --- rates come from an approved source, and there is not one yet ------------------
def test_the_shipped_rate_table_is_empty_and_says_why():
    """Prices come only from `research/cross-cutting/cloud-pricing.md` (r1 R45's rule, and
    this package's). That document prices GPU capacity and carries **no per-MTok row** for
    any judge model, so the approved table is empty, the estimate is unpriced, and the note
    carries the TO BE VERIFIED marker rather than a guess."""
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
                                {"input_per_million": "NaN"}, {"input_per_million": "0"},
                                {"output_per_million": "0"}, {"price_version": ""},
                                {"model": ""}, {"source": ""}, {"model": 5},
                                {"effective_at": "2026-01-01T00:00:00Z"},
                                {"effective_at": datetime(2026, 1, 1)}])
def test_a_rate_row_is_validated_at_the_boundary(kw):
    """R57: every rate is a **finite positive** decimal. A negative rate turns a debit into
    a credit; a **zero** rate makes a zero worst case that any budget covers, which is how
    an unbounded submission gets authorized; a float is not money; a naive `effective_at`
    cannot be compared with the store clock; and a row with no source is a price nobody
    approved."""
    with pytest.raises(ValueError):
        ProviderRate(**{**RATE_KW, **kw})


def test_a_rate_row_normalizes_to_the_money_scale():
    row = ProviderRate(**{**RATE_KW, "input_per_million": "5", "output_per_million": "25"})
    assert row.input_per_million == money.parse("5")
    assert StaticRateTable((row,)).rate_for("m", fakes.NOW) is row
    assert StaticRateTable((row,)).rate_for("other", fakes.NOW) is None


def test_the_rate_table_refuses_duplicate_rows_for_a_model():
    """R57: two rows for one model at one instant is an ambiguous price - which one applied
    would depend on list order, i.e. on whoever edited last."""
    with pytest.raises(errors.InvalidRequest):
        StaticRateTable((fakes.TEST_RATE, fakes.TEST_RATE))
    with pytest.raises(errors.InvalidRequest):
        StaticRateTable((fakes.TEST_RATE,
                         ProviderRate(**{**RATE_KW, "model": fakes.JUDGE_MODEL,
                                         "price_version": "other",
                                         "effective_at": fakes.TEST_RATE.effective_at})))
    with pytest.raises(errors.InvalidRequest):
        StaticRateTable(("not a rate",))
    # two rows for one model at *different* instants are a price history, not a duplicate
    assert StaticRateTable((fakes.TEST_RATE, fakes.NEWER_RATE)).rows


@pytest.mark.parametrize("rows", [(fakes.TEST_RATE, fakes.NEWER_RATE),
                                 (fakes.NEWER_RATE, fakes.TEST_RATE)])
def test_the_rate_effective_at_the_clock_is_used_whatever_the_row_order(rows):
    """R57. With "the first matching row wins", adding a newer, dearer row left every
    reservation at the old rate and under-reserved by the difference - and reversing the
    list silently changed the price. The row order must not be able to decide it."""
    table = StaticRateTable(rows)
    before_both = fakes.TEST_RATE.effective_at - timedelta(seconds=1)
    assert table.rate_for(fakes.JUDGE_MODEL, before_both) is None
    assert table.rate_for(fakes.JUDGE_MODEL, fakes.TEST_RATE.effective_at) is fakes.TEST_RATE
    assert table.rate_for(fakes.JUDGE_MODEL,
                          fakes.NEWER_RATE.effective_at - timedelta(seconds=1)) is fakes.TEST_RATE
    assert table.rate_for(fakes.JUDGE_MODEL, fakes.NEWER_RATE.effective_at) is fakes.NEWER_RATE
    assert table.rate_for(fakes.JUDGE_MODEL, fakes.NOW) is fakes.NEWER_RATE
    # and the dearer row really is dearer, so "which row" is a money question
    assert (per_sample_cost(fakes.NEWER_RATE, DEFAULT_CEILINGS)
            > per_sample_cost(fakes.TEST_RATE, DEFAULT_CEILINGS))
    assert estimate(table).price_version == "test-rates-v2"


def test_a_price_lookup_needs_an_aware_instant():
    with pytest.raises(errors.InvalidRequest):
        fakes.TEST_RATES.rate_for(fakes.JUDGE_MODEL, datetime(2026, 9, 21))


# --- the guard (R57) ---------------------------------------------------------------
def test_the_defaults_can_never_authorize_a_submission():
    """08 §5: `JUDGE_MODE=dry_run`, `JUDGE_LIVE_BUDGET_USD=0`. Each guard refuses **on its
    own account** (asserted by message), so flipping one setting is not enough to spend
    money."""
    priced = estimate()
    with pytest.raises(errors.BudgetExceeded, match="judge mode"):
        require_live_submission(DEFAULTS, priced, rates=fakes.TEST_RATES, at=fakes.NOW)
    assert allowed(DEFAULTS, priced) is False
    with pytest.raises(errors.BudgetExceeded, match="JUDGE_LIVE_BUDGET_USD"):
        require_live_submission(DEFAULTS.replace(judge_mode="live"), priced,
                                rates=fakes.TEST_RATES, at=fakes.NOW)
    with pytest.raises(errors.BudgetExceeded, match="judge mode"):
        require_live_submission(DEFAULTS.replace(judge_live_budget_usd=Decimal("100")), priced,
                                rates=fakes.TEST_RATES, at=fakes.NOW)
    assert allowed(LIVE, priced) is True
    assert require_live_submission(LIVE, priced, rates=fakes.TEST_RATES,
                                   at=fakes.NOW) == priced.worst_case_total


@pytest.mark.parametrize("mode", ["dry_run", "", "Live", "LIVE", "live ", "dryrun", "test",
                                  "anything"])
def test_only_the_exact_mode_live_authorizes(mode):
    """R57: matched exactly. "Anything but dry_run" authorized every typo of a mode name,
    which is the one class of configuration error that silently starts spending."""
    settings = DEFAULTS.replace(judge_mode=mode, judge_live_budget_usd=Decimal("100"))
    assert allowed(settings) is (mode == "live")


@pytest.mark.parametrize("budget", ["0", "-1", "0.00000000"])
def test_a_budget_that_is_not_positive_never_authorizes(budget):
    assert allowed(DEFAULTS.replace(judge_mode="live",
                                    judge_live_budget_usd=Decimal(budget))) is False


def test_a_pricing_estimate_without_a_hard_maximum_cannot_authorize_a_submission():
    """02, verbatim. An unpriced estimate is exactly that case: there is no maximum to
    reserve, so live submission stays disabled even in live mode with a budget."""
    unpriced = estimate(APPROVED_RATES)
    with pytest.raises(errors.BudgetExceeded, match="unpriced"):
        require_live_submission(LIVE, unpriced, rates=APPROVED_RATES, at=fakes.NOW)
    assert allowed(LIVE, unpriced, rates=APPROVED_RATES) is False


def test_an_estimate_priced_at_a_superseded_version_is_refused_even_at_the_same_amount():
    """R2-B4: the version check was untested at an *equal* total, so dropping it survived.
    `RENAMED_RATE` carries the same rates under a later version, which is exactly the
    rate-card edit that changes nothing about the money and everything about which
    approval a submission is running under."""
    priced = estimate(fakes.TEST_RATES)
    superseded = StaticRateTable((fakes.TEST_RATE, fakes.RENAMED_RATE))
    effective = superseded.rate_for(fakes.JUDGE_MODEL, fakes.NOW)
    assert effective is fakes.RENAMED_RATE
    assert worst_case(effective, priced.ceilings, priced.samples) == priced.worst_case_total, \
        "the amounts must be equal, so only the version can refuse"
    with pytest.raises(errors.BudgetExceeded, match="re-estimate"):
        require_live_submission(LIVE, priced, rates=superseded, at=fakes.NOW)
    assert allowed(LIVE, priced, rates=superseded) is False
    # and the same estimate against its own table still authorizes
    assert allowed(LIVE, priced, rates=fakes.TEST_RATES) is True


def test_an_estimate_with_no_price_version_is_not_priced():
    """R2-B4: `priced` dropping the version requirement survived, because an estimate with
    the correct amounts and **no version** was never tried. It authorizes nothing: the
    version is the approval the submission runs under, and `None` is not one."""
    correct = estimate()
    versionless = CostEstimate(model=correct.model, samples=correct.samples,
                               ceilings=correct.ceilings, price_version=None,
                               per_sample=correct.per_sample,
                               worst_case_total=correct.worst_case_total)
    assert versionless.priced is False
    with pytest.raises(errors.BudgetExceeded, match="unpriced"):
        require_live_submission(LIVE, versionless, rates=fakes.TEST_RATES, at=fakes.NOW)
    assert allowed(LIVE, versionless) is False
    for empty in ("", " "):
        blank = CostEstimate(model=correct.model, samples=correct.samples,
                            ceilings=correct.ceilings, price_version=empty,
                            per_sample=correct.per_sample,
                            worst_case_total=correct.worst_case_total)
        assert allowed(LIVE, blank) is False, empty


def test_a_withdrawn_rate_disables_submission_even_for_a_priced_estimate():
    """The estimate is a report from a moment ago; the guard asks the table again. A rate
    withdrawn in between is exactly the state in which nothing may be submitted."""
    priced = estimate()
    assert allowed(LIVE, priced, rates=fakes.TEST_RATES) is True
    assert allowed(LIVE, priced, rates=APPROVED_RATES) is False
    # ...and so is an estimate priced at a version that is no longer effective
    assert allowed(LIVE, priced, rates=StaticRateTable((fakes.TEST_RATE, fakes.NEWER_RATE))) \
        is False


def test_the_guard_recomputes_the_total_and_never_trusts_the_estimate():
    """R57, the heart of it. A `CostEstimate` is a report: these four forgeries each used
    to authorize a submission because the guard read `worst_case_total` (or fell back to
    zero when it was absent) instead of recomputing `ceiling(per_sample x samples)`."""
    ceilings = DEFAULT_CEILINGS
    forgeries = (
        CostEstimate(model=fakes.JUDGE_MODEL, samples=50, ceilings=ceilings,
                     price_version="test-rates-v1", per_sample=Decimal("999")),
        CostEstimate(model=fakes.JUDGE_MODEL, samples=50, ceilings=ceilings,
                     price_version="test-rates-v1", per_sample=Decimal("999"),
                     worst_case_total=Decimal("-5")),
        CostEstimate(model=fakes.JUDGE_MODEL, samples=1_000_000, ceilings=ceilings,
                     price_version="test-rates-v1", per_sample=Decimal("999"),
                     worst_case_total=Decimal("0.01")),
        CostEstimate(model=fakes.JUDGE_MODEL, samples=50, ceilings=ceilings,
                     price_version="test-rates-v1", per_sample=Decimal("0.00000001"),
                     worst_case_total=Decimal("0.00000001")),
    )
    for forged in forgeries:
        assert allowed(LIVE, forged) is False, forged
        with pytest.raises(errors.BudgetExceeded):
            require_live_submission(LIVE, forged, rates=fakes.TEST_RATES, at=fakes.NOW)
    # none of them is even `priced`, and the honest estimate is
    assert [f.priced for f in forgeries] == [False, False, True, True]
    assert estimate().priced is True


def test_a_zero_or_negative_sample_count_never_authorizes():
    assert allowed(LIVE, estimate(samples=0)) is False


def test_a_worst_case_over_the_budget_is_refused_and_the_boundary_is_inclusive():
    """R57: `<=` the budget. At exactly the budget a submission is affordable; one unit of
    scale over it is not, which is the off-by-one a `>=` would introduce."""
    exact = estimate(samples=50)
    total = exact.worst_case_total
    assert allowed(DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=total)) is True
    assert allowed(DEFAULTS.replace(judge_mode="live",
                                    judge_live_budget_usd=total - money.SCALE)) is False
    small = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("0.01"))
    assert allowed(small, exact) is False


# --- import hygiene: no provider SDK on the dry-run path ---------------------------
PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "infrx"


def test_the_judge_package_never_imports_the_provider_sdk():
    """A guard alone is not enough: an import at module scope builds no client but it does
    load the SDK into every process that touches the judge, and the next refactor puts a
    client next to it. The source is the check."""
    sdk_import = re.compile(r"^\s*(?:import|from)\s+anthropic\b", re.MULTILINE)
    offenders = [path.name for path in sorted((PACKAGE / "judge").rglob("*.py"))
                 if sdk_import.search(path.read_text())]
    assert offenders == []
    # and the check is not vacuous: it fires on the line it exists to forbid
    assert sdk_import.search("from anthropic import Anthropic") is not None
    assert sdk_import.search("    import anthropic  # deferred") is not None


def test_importing_the_dry_run_path_loads_no_provider_sdk():
    """Checked in a fresh interpreter, because another test in the same session could have
    imported the SDK for its own reasons and hidden this."""
    script = ("import sys\n"
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
