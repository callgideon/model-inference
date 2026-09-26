#!/usr/bin/env python3
"""WR-W5F5-3 (W5-F5B): `admit_ready` refuses what 0019 `check_pinned_capability` refuses.

    uv run --frozen pytest -q tests/contracts/v2/test_lifecycle_capability.py
    INFRX_D_TASK=e2c uv run --frozen pytest -q tests/contracts/v2/test_lifecycle_capability.py

The same cases run against the fake (`FakeLifecycle`) and, with a task-local PostgreSQL,
against D10's `PgLifecycle`: a fake that admits what the SQL refuses lets a relay test pass
on a world the deployment never has. The refusal-reason table is W5-F5's
(`research/plan/evidence/w/W5-F5-3a6195b.md`). Oracle: the fake before W5-F5B checked only
video and stream, so a text part outside the pinned revision's `input_modalities` was
admitted (a job, a hold and a marker), and a pinned revision the catalog no longer answers
was an `AttributeError` rather than `not_found`.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import lifecycle as suite
from infrx.contracts.conformance.harness import hook
from infrx.contracts.fakes.factories import lifecycle_factory

IDS = suite.IDS


async def capability__a_part_outside_the_pinned_modalities_admits_nothing(factory):
    """The pinned revision stops taking text: a text-only request and a text+video one are
    both `unsupported_media` (`param=messages`), with no job and no money moved, in both
    regimes (a legacy `<alias>@<label>` names that revision too); with text back, the same
    request admits. Checked for every part, not only for media."""
    harness = factory()
    port = harness.port
    before = suite._balances(harness)
    clip = await suite._staged(harness, b"text-withdrawn")
    hook(harness, "set_capability")(IDS.serving_version, ("video",))
    for n, (refs, expectation) in enumerate(
            (r, e) for r in ((), (clip,)) for e in suite.EXPECTATIONS):
        request = suite._request(harness, refs)
        idem = suite._idem(request, f"text-withdrawn-{n}")
        try:
            await port.admit_ready(request, idem, expectation)
        except errors.UnsupportedMedia as refused:
            assert refused.param == "messages", refused.param
        else:
            raise AssertionError(f"a video-only revision admitted text ({len(refs)} refs)")
        await suite._nothing_admitted(harness, request, idem, before)
    hook(harness, "set_capability")(IDS.serving_version, ("text", "video"))
    request = suite._request(harness, (clip,))
    admission, readiness = await port.admit_ready(request, suite._idem(request, "text-back"),
                                                  suite.CARD)
    assert readiness is not None and not admission.replayed


CASES = (capability__a_part_outside_the_pinned_modalities_admits_nothing,)


@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
def test_the_fake_refuses_what_check_pinned_capability_refuses(case):
    asyncio.run(case(lifecycle_factory))


def test_the_fake_answers_a_pinned_revision_missing_from_the_catalog_not_found():
    """0019 `check_pinned_capability`: `v_cap is null` -> `not_found`. The fake pins through
    the catalog, then reads the pinned revision's capability; a catalog that no longer
    answers it is `not_found` with nothing admitted (fake only: in PostgreSQL the pin and
    the read are one transaction over a row the job's foreign key holds)."""
    harness = lifecycle_factory()
    catalog = hook(harness, "jobs").catalog
    reads = []
    answer = catalog.serving_revision

    async def gone_after_the_pin(serving_version_id):
        reads.append(serving_version_id)
        return await answer(serving_version_id) if len(reads) == 1 else None

    catalog.serving_revision = gone_after_the_pin
    before = suite._balances(harness)
    request = suite._request(harness)
    idem = suite._idem(request, "revision-gone")
    with pytest.raises(errors.NotFound, match="pinned serving revision"):
        asyncio.run(harness.port.admit_ready(request, idem, suite.CARD))
    assert len(reads) == 2, reads
    catalog.serving_revision = answer
    asyncio.run(suite._nothing_admitted(harness, request, idem, before))


# --- the same cases against D10's PostgreSQL adapter (task-local; skipped without it) -----
def _pg_factory():
    if not os.environ.get("INFRX_D_TASK"):
        pytest.skip("PostgreSQL only on an explicit task-local block (INFRX_D_TASK)")
    from infrx.state import migrations, pgtesting
    from tests.d import pgharness, pgstore
    reason = pgharness.unavailable()
    if reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    return pgtesting.make_lifecycle_factory(pgstore.fresh_database, pgharness.dsn,
                                            migrations.SEED_MARLIN.read_text())


@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
def test_postgres_refuses_the_same(case):
    asyncio.run(case(_pg_factory()))
