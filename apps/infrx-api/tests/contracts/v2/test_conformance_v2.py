#!/usr/bin/env python3
"""F-CONTRACT v2: every exported v2 conformance case, against the fake directories.

Each test id is the oracle plus the invariant, so `-k credit_rate` runs everything
that serves CREDIT-RATE and D1R/G1R can see which cases their adapters must also
pass. Green here means implemented, never integrated.

This is the file `mutants_v2.py` selects from: a mutant is killed only when a case
named **here** fails.

    uv run --frozen pytest -q tests/contracts/v2/test_conformance_v2.py
    uv run --frozen pytest -q tests/contracts/v2/test_conformance_v2.py -k lab_access
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from infrx.contracts.conformance import v2_contracts
from infrx.contracts.conformance.v2_fakes import fake_v2_harness
from infrx.contracts.v2 import ports as v2ports

CASES = v2_contracts.cases()

ORACLES = ("split_contract", "credit_units", "credit_identity", "credit_grant", "credit_rate",
           "lab_access")


@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
def test_the_fakes_pass_every_v2_case(case):
    asyncio.run(case(fake_v2_harness))


def test_every_case_names_its_oracle_and_its_invariant():
    """A case whose name does not carry an oracle cannot be found by the evidence
    report, and one without a docstring states no invariant to be killable about."""
    for case in CASES:
        assert case.__name__.split("__")[0] in ORACLES, case.__name__
        assert case.__doc__, f"{case.__name__} states no invariant"
        assert inspect.iscoroutinefunction(case), case.__name__
    assert len({case.__name__ for case in CASES}) == len(CASES), "duplicate case names"
    covered = {case.__name__.split("__")[0] for case in CASES}
    assert covered == set(ORACLES), f"no case for {sorted(set(ORACLES) - covered)}"


def test_the_exported_runner_runs_the_whole_suite():
    """The entry point D1R/G1R call with their own harness factory."""
    assert v2_contracts.run_v2_conformance(fake_v2_harness) == len(CASES)


def test_each_case_gets_a_fresh_harness():
    """A case that publishes a rate card or revokes a grant must not leak into the
    next one, so the factory is called per case and never memoised."""
    first, second = fake_v2_harness(), fake_v2_harness()
    assert first is not second and first.catalog is not second.catalog
    card = asyncio.run(first.catalog.active_rate_card(
        next(iter(first.catalog.rate_cards))))
    first.catalog.rate_cards.clear()
    assert asyncio.run(second.catalog.active_rate_card(card.deployment_revision_id)) is not None


def test_the_fakes_satisfy_the_three_v2_protocols():
    harness = fake_v2_harness()
    assert isinstance(harness.wallets, v2ports.WalletDirectory)
    assert isinstance(harness.catalog, v2ports.CatalogDirectory)
    assert isinstance(harness.providers, v2ports.ProviderDirectory)
    for directory, protocol in ((harness.wallets, v2ports.WalletDirectory),
                                (harness.catalog, v2ports.CatalogDirectory),
                                (harness.providers, v2ports.ProviderDirectory)):
        for name in protocol.__protocol_attrs__:
            operation = getattr(directory, name)
            assert inspect.iscoroutinefunction(operation), f"{name} is not async"
