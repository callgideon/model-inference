"""Canonical JSON form shared by every record, envelope and fixture.

One rule, so a fixture on disk, a wire body and a database payload are the same
bytes: sorted keys, two-space indent, trailing newline, `None` omitted (a field
that is absent reads back as its `None` default), decimals as fixed-point
strings (`money`), datetimes as RFC 3339 with `Z` (`records`).
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def canonical_obj(value: Any) -> Any:
    """JSON-ready plain data for a model (or pass plain data straight through)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def canonical_bytes(value: Any) -> bytes:
    """The one on-disk/on-wire spelling of a JSON document."""
    return (json.dumps(canonical_obj(value), sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def compact_bytes(value: Any) -> bytes:
    """Same content without the pretty printing: SSE data fields, PG payloads."""
    return json.dumps(canonical_obj(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
