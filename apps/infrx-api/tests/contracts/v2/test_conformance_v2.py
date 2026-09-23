#!/usr/bin/env python3
"""F-CONTRACT v2: every exported v2 conformance case, against the fake directories.

Each test id is the oracle plus the invariant, so `-k credit_rate` runs everything
that serves CREDIT-RATE and D1R/G1R can see which cases their adapters must also
pass. Green here means implemented, never integrated.

This is the v2 target of the one mutation runner (`tests/contracts/mutants.py`): a v2
mutant is killed only when a case named **here** fails.

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


def test_the_conformance_package_exports_the_v2_suite():
    """F2P wire-in item 2: a track reaches the v2 suite from the same package as v1's,
    and `V2_SUITES` runs the whole exported list, not a subset of it."""
    from infrx.contracts import conformance
    assert "run_v2_conformance" in conformance.__all__
    assert "V2_SUITES" in conformance.__all__
    cases, runner = conformance.V2_SUITES["v2"]
    assert [case.__name__ for case in cases()] == [c.__name__ for c in v2_contracts.cases()]
    assert runner(fake_v2_harness) == len(v2_contracts.cases())


# --- the CREDIT regime of the JobStore (F2P wire-in, items 3-4) ----------------------
CREDIT_CASES = v2_contracts.credit_jobstore_cases()


@pytest.mark.parametrize("case", CREDIT_CASES, ids=[case.__name__ for case in CREDIT_CASES])
def test_the_fake_store_passes_every_credit_jobstore_case(case):
    from infrx.contracts.fakes.factories import credit_jobstore_factory
    asyncio.run(case(credit_jobstore_factory))


def test_the_fake_store_satisfies_both_jobstore_protocols():
    """One store, two regimes: the v1 port stays whole beside its CREDIT sibling."""
    from infrx.contracts import ports
    from infrx.contracts.fakes.factories import V2_FACTORIES, credit_jobstore_factory
    store = credit_jobstore_factory().port
    assert isinstance(store, ports.JobStore) and isinstance(store, ports.CreditJobStore)
    assert set(V2_FACTORIES) == set(__import__("infrx.contracts.conformance",
                                               fromlist=["V2_SUITES"]).V2_SUITES)


def test_work_v2_carries_the_prompt_count_within_the_admitted_ceiling():
    """`WorkV2.prompt_tokens` mirrors v1 `Work.prompt_tokens`: never past the ceiling the
    CREDIT hold was sized for."""
    import pydantic
    from infrx.contracts.v2 import fixtures as v2fix, records as v2
    work = v2fix.BUILDERS["work.json"]()
    ceiling = work.request.request.max_input_tokens
    body = work.model_dump(mode="json")
    assert v2.WorkV2.model_validate({**body, "prompt_tokens": ceiling}).prompt_tokens == ceiling
    with pytest.raises(pydantic.ValidationError):
        v2.WorkV2.model_validate({**body, "prompt_tokens": ceiling + 1})


def test_a_pre_cutover_row_keeps_its_absences_and_a_credit_row_cannot_have_them():
    """Item 10, found against D1R's real 0001-0005 rows: a legacy row may have recorded no
    tokens and no settlement state, and the projection keeps both absent instead of inventing
    `settled`; a CREDIT row, which the metering path always settles, cannot omit either."""
    import pydantic
    from infrx.contracts.v2 import fixtures as v2fix, records as v2
    raw = {"request_id": "90000000-0000-4000-8000-000000000003", "cost_usd": "1.99999999",
           "prompt_tokens": None, "completion_tokens": None, "usage_certainty": None,
           "settlement_state": None, "price_version": None,
           "settled_at": "2026-09-20T00:00:00Z"}
    legacy = v2.project_v1_usage(raw, org_id=v2fix.IDS.consumer_org)
    assert (legacy.usage, legacy.outcome) == (None, None)
    assert str(legacy.amount()) == "1.99999999"
    tokens = v2.project_v1_usage({**raw, "prompt_tokens": 1000, "completion_tokens": 250},
                                 org_id=v2fix.IDS.consumer_org)
    assert tokens.usage is not None and tokens.usage.total_tokens == 1250
    assert tokens.outcome is None
    # F2P review M-7: a half-recorded usage report is refused, never dropped or completed.
    for half in ({"prompt_tokens": 1000}, {"completion_tokens": 250}):
        with pytest.raises(ValueError, match="only one token count"):
            v2.project_v1_usage({**raw, **half}, org_id=v2fix.IDS.consumer_org)
    body = v2fix.BUILDERS["usage_credit.json"]().model_dump(mode="json")
    for missing in ("usage", "outcome"):
        with pytest.raises(pydantic.ValidationError):
            v2.UsageRecordV2.model_validate({k: v for k, v in body.items() if k != missing})
