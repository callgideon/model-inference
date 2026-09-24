"""Contracts v2 — the product amendment of 2026-09-21, encoded.

Additive: v1 stays exactly where it is and keeps working. Every record here is
`schema_version: 2` and nothing in this package is imported by a v1 module, so a
track that has not migrated yet is unaffected.

What v2 adds over v1 (the field-by-field map is
`research/plan/01a-contracts-v2-map.md` and `fixtures/v2/map.json`):

* CREDIT as a **unit**, not a relabelled USD: `money_units` has three distinct
  runtime-validated denominations and no conversion between them.
* Credential *audience* (consumer / provider_dev / operator) and a wallet the
  server derives from trusted data; no request field can name a wallet.
* Admission pins: model -> deployment revision -> serving version + rate card
  version + policy version, frozen at acceptance.
* The individual signup grant, keyed by user identity alone.
* Provider roles and source-purpose grants, default deny, checked against the
  *current* grant rather than a snapshot.

Submodules load on first attribute access, like `infrx.contracts` itself, so
`import infrx.contracts.v2` reaches no third-party package beyond pydantic.
"""
from __future__ import annotations

import importlib
from typing import Any

# The one reviewed identifier for the whole changed consumer/provider surface
# (F2P item 7). The console half declares the same string in
# `apps/app/lib/contracts/v2/money-units.ts`; `tests/contracts/v2/test_parity_v2.py`
# fails if the two drift.
SURFACE_VERSION = "contracts-v2.0"

SCHEMA_VERSION = 2

_SUBMODULES = ("fixtures", "lifecycle", "money_units", "ports", "records")

__all__ = ["SCHEMA_VERSION", "SURFACE_VERSION", *_SUBMODULES]


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
