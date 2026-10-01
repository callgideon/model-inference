"""The Lab's `...Z` instant (A6): replaces the hand-rolled `strftime("%Y-%m-%dT%H:%M:%SZ")`
formatters of the Lab routes, workers and `pilot._z`. The wire's microsecond form stays
`contracts.records._rfc3339`."""
from __future__ import annotations

from datetime import UTC, datetime


def iso_z(value: datetime | str) -> str:
    """UTC to the second; ISO text (the database's) is read first, a naive instant is UTC."""
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if at.tzinfo is not None:
        at = at.astimezone(UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")
