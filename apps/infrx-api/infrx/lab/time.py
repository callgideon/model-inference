"""The Lab's `...Z` instant (A6): replaces the hand-rolled `strftime("%Y-%m-%dT%H:%M:%SZ")`
formatters. Call sites: the four Lab routes (`routes/lab_datasets` derive's `created_at`,
`lab_evaluations`' `created_at`, `lab_releases`' `proposed_at`, `lab_pipelines`' `approved_at`);
`rollouts/control` `_decide`'s `decided_at` (WR-L4-3) and `evaluation/checkpoints`'
resumed-run `issued_at` (WR-L4-4), both at merge #79; `gateway/pilot._z` (WR-L4-1) and
`lab/workers/__main__`'s `decided_at` (WR-L4-2) follow through api-L1. The wire's
microsecond form stays `contracts.records._rfc3339`."""
from __future__ import annotations

from datetime import UTC, datetime


def iso_z(value: datetime | str) -> str:
    """UTC to the second; ISO text (the database's) is read first, a naive instant is UTC."""
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if at.tzinfo is not None:
        at = at.astimezone(UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")
