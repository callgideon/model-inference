#!/usr/bin/env python3
"""lab-sql LW9: the Python half of 0058's ports with NO database - what `PgLabVariants`
(the identified listing, `put_identities`) and `PgReleaseStore.tally` send and return, and
R3's `store` writing both identities at the variant's creation. `tests/l3sql/mutants.py` runs
here; the SQL is `test_lw9.py`.

    uv run --frozen pytest -q tests/l3sql/test_lw9_units.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records as lab
from infrx.rollouts import optimization as r3
from infrx.state.lab_rollout import PgReleaseStore
from infrx.state.lab_variants import PgLabVariants

from ..d.test_d7_units import NEMO, _ok, _sent
from ..r.optimization import test_optimization as r3w
from .test_lw7_units import _on


def test_variants__are_read_for_the_callers_provider_with_their_identities() -> None:
    """WR-LW7-3a: the listing is 0058's identified one, asked for the caller's provider and
    returned whole."""
    rows = [{"variant_ref": "lab:variant:x", "comparison": None, "base": None, "variant": None}]
    store, conn = _on(PgLabVariants, rows)
    assert _ok(store.variants(NEMO)) == rows
    assert _sent(conn) == ("lab_optimization_variant_listing", {"provider_org_id": NEMO})


def test_put_identities__sends_both_identities_for_the_providers_variant() -> None:
    answer = {"variant_ref": "lab:variant:x", "base": {"engine": "a"}, "variant": {"engine": "b"}}
    store, conn = _on(PgLabVariants, answer)
    assert _ok(store.put_identities("lab:variant:x", base={"engine": "a"},
                                    variant={"engine": "b"}, provider_org_id=NEMO,
                                    actor="r3")) == answer
    assert _sent(conn) == ("lab_put_variant_identities", {
        "variant_ref": "lab:variant:x", "base": {"engine": "a"}, "variant": {"engine": "b"},
        "provider_org_id": NEMO, "actor": "r3"})


def test_tally__is_read_for_the_revision_as_answered() -> None:
    """WR-C7-TALLY: the tally is asked for this revision and returned whole."""
    rows = [{"serving_ref": "s", "pinned_by": "cohort", "requests": 3}]
    store, conn = _on(PgReleaseStore, rows)
    assert _ok(store.tally("lab:policy:p")) == rows
    assert _sent(conn) == ("lab_release_tally", {"policy_ref": "lab:policy:p"})


class Variants:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    async def put_identities(self, ref, *, base, variant, provider_org_id, actor):
        self.calls.append(("put_identities", ref, base, variant, provider_org_id, actor))


def test_r3_store__writes_both_identities_as_the_variant_is_created() -> None:
    """R3 stores the registered base and variant identities (JSON dumps) right after the
    variant record, before the report and the comparison; identities that are not the
    registered ones (swapped, or another revision), no identities (WR-LW9-4: every variant R3
    creates carries both) or no port to store them through (F3) write nothing."""
    rep = r3w.report()
    result = r3w.compare(rep=rep)
    data = r3w.Data()
    ids = Variants(data.calls)
    asyncio.run(r3.store(data, r3w.VARIANT, result, rep, provider_org_id=r3w.P, actor="dev@p",
                         identities=(r3w.BASE, r3w.NVFP4), variants=ids))
    assert [c[0] for c in data.calls] == ["publish", "put_identities", "put_eval_report",
                                          "put_variant_comparison"], data.calls
    assert data.calls[1] == ("put_identities", lab.ref_of(r3w.VARIANT),
                             r3w.BASE.model_dump(mode="json"), r3w.NVFP4.model_dump(mode="json"),
                             r3w.P, "dev@p")
    for wrong in ((r3w.NVFP4, r3w.BASE), (r3w.BASE, r3w.ident(quantization="fp8"))):
        untouched = r3w.Data()
        with pytest.raises(errors.InvalidRequest, match="not the registered ones"):
            asyncio.run(r3.store(untouched, r3w.VARIANT, result, rep, provider_org_id=r3w.P,
                                 actor="dev@p", identities=wrong,
                                 variants=Variants(untouched.calls)))
        assert untouched.calls == [], wrong
    for missing in ({"identities": (r3w.BASE, r3w.NVFP4), "variants": None},    # F3
                    {"identities": None, "variants": Variants([])}):          # WR-LW9-4
        untouched = r3w.Data()              # nothing written; dies on an assertion, never
        try:                                # an AttributeError/TypeError
            asyncio.run(r3.store(untouched, r3w.VARIANT, result, rep, provider_org_id=r3w.P,
                                 actor="dev@p", **missing))
            refused = None
        except Exception as answer:         # noqa: BLE001 - the answer is the oracle
            refused = answer
        assert isinstance(refused, errors.InvalidRequest) and "both identities" in str(refused) \
            and untouched.calls == [], (missing, refused, untouched.calls)
