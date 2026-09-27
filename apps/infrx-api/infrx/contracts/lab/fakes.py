"""F3 fake adapter: the Lab catalog in memory, with the real adapter's rules.

Content-addressed (publishing the same bytes twice is one ref), immutable (a ref names
exactly one record for ever), provider-scoped (another provider's ref is `NotFound`, the
same answer as an unknown one). A real adapter (D7) keeps these three rules in SQL.
"""
from __future__ import annotations

from typing import Any

from .. import errors
from . import records


class FakeLabCatalog:
    def __init__(self) -> None:
        self._rows: dict[str, records.LabRecord] = {}

    def publish(self, payload: dict[str, Any]) -> str:
        ref = records.ref_of(payload)            # refuses anything `parse` refuses
        self._rows.setdefault(ref, records.parse(payload))
        return ref

    def resolve(self, ref: str, *, provider_org_id: str) -> records.LabRecord:
        """`provider_org_id` is the caller's server-derived provider, never a request field."""
        record = self._rows.get(ref)
        if record is None or record.provider_org_id != provider_org_id:
            raise errors.NotFound("no such Lab record for this provider")
        return record

    def refs(self) -> list[str]:
        return sorted(self._rows)
