#!/usr/bin/env python3
"""AP-10 10c (the E6L j10 probe): LAB-E2E evaluate's `world.composed` must say a port is
carried only when the control unit's own composition answers through it. Since WR-AP10-1
the unit composes `catalog`; since WR-UXVF-1 its listing is 0066's, and a database that does
not answer is still a 503 (`DependencyUnavailable`): counted as carried, j10 would bind "for
real" over the route suite's fake catalog - a PASS no composition earns. The probe asks the
port.

    uv run --frozen pytest -q tests/ap10/test_e2e_probe.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
from types import SimpleNamespace

from infrx.contracts import errors
from infrx.lab import evaluation as ev

BACKEND = pathlib.Path(__file__).resolve().parents[3] / "lab/tests/e2e/evaluate/backend.py"


def _backend():
    spec = importlib.util.spec_from_file_location("lab_e2e_evaluate_backend", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ap10_j10_a_catalog_answering_503_is_not_carried():
    """Oracle: the unit's composed `Catalog` over a store answering 0066's listing -> carried;
    over one answering 503 -> NOT carried (j10 stays NOT RUN); an absent port is not. A probe
    on `is not None` alone reports the 503 catalog carried."""
    backend = _backend()
    listing = {"datasets": [], "harnesses": [], "servings": [], "evaluators": []}

    class Store:
        def __init__(self, fail=None):
            self.fail = fail

        async def eval_catalog(self, *, provider_org_id):
            if self.fail:
                raise self.fail
            return listing
    unit = SimpleNamespace(store=object(), experiments=object(), ledger=object(),
                           catalog=ev.Catalog(Store()))
    assert asyncio.run(backend.carried(unit, "nemo")) == \
        {"store": True, "experiments": True, "catalog": True, "ledger": True}
    absent = SimpleNamespace(store=object(), experiments=None, ledger=object(),
                             catalog=ev.Catalog(Store()))
    assert asyncio.run(backend.carried(absent, "nemo")) == \
        {"store": True, "experiments": False, "catalog": True, "ledger": True}
    down = SimpleNamespace(store=object(), experiments=object(), ledger=object(),
                           catalog=ev.Catalog(Store(errors.DependencyUnavailable("down"))))
    assert asyncio.run(backend.carried(down, "nemo")) == \
        {"store": True, "experiments": True, "catalog": False, "ledger": True}
