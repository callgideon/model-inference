"""AP-02 pure decisions (no PostgreSQL): the result read outcome, page sizes, request filters.

Oracles: the read outcome is request-view-model.ts `resultAccessOf` exactly (a non-terminal job
is pending, held_unknown is withheld, only an authoritative success has a result, a missing
expiry is unavailable, the database's flag decides available vs expired); a page is 25 by
default and 1..100 otherwise; a filter that is not a key id or an instant with an offset is
refused, and the canonical form is what scopes a cursor.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from infrx.console import reads
from infrx.contracts import errors

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


@pytest.mark.parametrize(("state", "settlement", "certainty", "expires", "available", "want"), [
    ("running", None, None, None, False, "pending"),
    ("queued", "settled", "authoritative", NOW, True, "pending"),
    ("succeeded", "held_unknown", "unknown", NOW, True, "held_unknown"),
    ("failed", "released_free", None, None, False, "no_result"),
    ("cancelled", "settled", "authoritative", NOW, True, "no_result"),
    ("succeeded", "settled", "estimated", NOW, True, "no_result"),
    ("succeeded", "settled", "authoritative", None, False, "unavailable"),
    ("succeeded", "settled", "authoritative", NOW, False, "expired"),
    ("succeeded", "settled", "authoritative", NOW, True, "available"),
])
def test_units__result_access_is_the_read_outcome(state, settlement, certainty, expires,
                                                   available, want):
    assert reads.result_access(state, settlement, certainty, expires, available) == want


def test_units__page_size_defaults_to_25_and_refuses_outside_1_to_100():
    assert (reads.page_size(None), reads.page_size("1"), reads.page_size("100")) == (25, 1, 100)
    for bad in ("0", "101", "", "1.5", "-3", "ten"):
        with pytest.raises(errors.InvalidRequest):
            reads.page_size(bad)


def test_units__request_filters_are_validated_and_canonical():
    got = reads.request_filters("m", "C7000000-0000-4000-8000-0000000000F1",
                                "2026-09-20T11:00:00Z", "2026-09-20T13:00:00+02:00")
    assert got == {"model": "m", "key_id": "c7000000-0000-4000-8000-0000000000f1",
                   "from": "2026-09-20T11:00:00+00:00", "to": "2026-09-20T11:00:00+00:00"}
    for kw in ({"key_id": "nope"}, {"from_": "yesterday"}, {"to": "2026-09-20T11:00:00"},
               {"model": ""}, {"model": "m" * 201}):
        args = {"model": None, "key_id": None, "from_": None, "to": None} | kw
        with pytest.raises(errors.InvalidRequest):
            reads.request_filters(**args)


def test_units__every_route_is_documented_for_the_openapi_export():
    """R270: each route names its operation, its response model and the error envelope, so
    AP-00's export documents the whole family (12 GET routes, nothing else)."""
    from types import SimpleNamespace

    from fastapi import FastAPI

    from infrx.gateway.routes import console_reads

    async def never():
        raise AssertionError("no read happens while documenting")
    app = FastAPI()
    console_reads.register(app, SimpleNamespace(
        actors=None, console_reads=reads.ConsoleReads(never, b"ap02-console-cursor-secret")))
    paths = app.openapi()["paths"]
    assert len(paths) == 12 and all(set(ops) == {"get"} for ops in paths.values()), paths
    for path, ops in paths.items():
        op = ops["get"]
        assert op["operationId"] and "$ref" in str(op["responses"]["200"]), path
        assert "ErrorEnvelope" in str(op["responses"].get("503")), path
