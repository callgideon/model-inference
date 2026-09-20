"""Contracts v1, executable. Coordinator-owned: a change here is a contract revision.

Submodules load on first attribute access (`contracts.money`) or by direct
import (`from infrx.contracts import money`). Nothing is imported eagerly, so
`import infrx` still reaches no third-party package and reads no environment -
the F1 baseline asserts exactly that, and `infrx/config.py` imports
`contracts.limits` for the pilot configuration names.

`import infrx.contracts` must never pull in psycopg, valkey, clickhouse-connect,
boto3 or anthropic: those belong to a track's extra, not to the request path.
`fakes` and `conformance` are test-time modules; import them explicitly.

See README.md in this directory for the encoding decisions and for how a track
runs the conformance suite against its own adapter.
"""
from __future__ import annotations

import importlib
from typing import Any

_SUBMODULES = ("codec", "errors", "ids", "limits", "money", "ports", "records", "wire")

__all__ = list(_SUBMODULES)


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
