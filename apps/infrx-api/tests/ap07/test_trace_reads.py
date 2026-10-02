#!/usr/bin/env python3
"""AP-07c: what `/lab/v1/traces` says about each request - the access state, the measured
timing, the pins - and its server-side filters bound into the cursor.

    uv run --frozen pytest -q tests/ap07/test_trace_reads.py
    INFRX_D_TASK=ap7 uv run --frozen pytest -q tests/ap07/test_trace_reads.py   # + the pg half

The world and the projection are WR-V1M-2's (`tests/g/lab_traces/test_lab_traces.py`): A's
serving version carries C1's (granted after `grant_content`) and C2's (never granted) requests,
a lost capture, one past its content bound and one past its metadata bound.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.gateway.routes import lab_traces as lt
from infrx.traces import ship

from ..g import support
from ..g.lab_traces.test_lab_traces import (FAKE_SERVING, NOW, Serving, Sessions, by_request,
                                            client, get, grant_content, listed, traces)
from ..l.access.worlds import FakeWorld

R = "test_trace_reads__"
OTHER_SERVING = "5e000000-0000-4000-8000-0000000000cc"


def run(coro):
    return asyncio.run(coro)


def variant(t, name: str, like: str, **changes) -> None:
    """Another request shaped like `like`'s row, with `changes` (a minimal capture, an answer
    that broke off, an unfinished one, another serving version)."""
    [row] = [r for r in t.rows.inserted if r.request_id == t.ids[like]]
    n = len(t.ids) + 1
    request_id = f"{n:08x}-0000-4000-8000-000000000001"
    trace_id = f"seg-{n:04d}:0"
    changes.setdefault("content_key", row.content_key and ship.content_key(row.org_id, trace_id))
    if changes["content_key"]:
        run(t.objects.put_if_absent(changes["content_key"], b'{"prompt":"variant"}',
                                    "application/json"))
    run(t.rows.insert([dataclasses.replace(row, request_id=request_id, trace_id=trace_id,
                                           **changes)]))
    t.ids[name] = request_id


def states(c, w, t, **params) -> dict[str, str]:
    return {name: item["access_state"]
            for name, item in by_request(t, listed(c, w.DEV_A, w.A, **params)["data"]).items()}


# --- the access state ------------------------------------------------------------------------
def test_trace_reads__each_row_states_why_its_content_is_or_is_not_there(world):
    """Oracle: one state per reason, never two reasons folded into one - captured and granted
    `content`; never granted `metadata`; capture lost or below full `not_captured`; past the
    content bound `expired`; an answer that broke off `partial`. The detail agrees with the
    list, and only `content`/`partial` carry content."""
    w = world
    t = traces(w)
    variant(t, "minimal", "granted", mode="minimal", content_key=None, content_stored=False,
            content_bytes=0)
    variant(t, "broken", "granted", content_complete=False, loss_reason="none")
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    assert states(c, w, t) == {"granted": "content", "ungranted": "metadata",
                               "lost": "not_captured", "expired": "expired",
                               "minimal": "not_captured", "broken": "partial"}
    for name, state in states(c, w, t).items():
        detail = get(c, w.DEV_A, w.A, "/" + t.ids[name]).json()
        assert detail["access_state"] == state, name
        assert (detail.get("content") is not None) == (state in ("content", "partial")), name


def test_trace_reads__a_lapsed_grant_is_revoked_not_never_granted(world):
    """Oracle: once the grantor revokes, its rows say `revoked` - distinct from the grantor
    that never granted (`metadata`) - while a capture below full stays `not_captured`
    whatever the grant."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    w.revoke_grant(w.C1, w.A)
    c = client(w, t)
    got = states(c, w, t)
    assert (got["granted"], got["expired"], got["ungranted"], got["lost"]) \
        == ("revoked", "revoked", "metadata", "revoked")
    variant(t, "minimal", "granted", mode="minimal", content_key=None, content_stored=False)
    assert states(client(w, t), w, t)["minimal"] == "not_captured"


def test_trace_reads__a_revocation_between_list_and_detail_withholds_content(world):
    """Oracle: the grant is read again for the detail - a list page that showed content is no
    licence for the next read: revoked in between, the detail is `revoked`, with no content,
    grantor or grant reference."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    assert states(c, w, t)["granted"] == "content"
    w.revoke_grant(w.C1, w.A)
    detail = get(c, w.DEV_A, w.A, "/" + t.ids["granted"]).json()
    assert detail["access_state"] == "revoked"
    assert not {"content", "grantor_org_id", "grant_ref"} & set(detail), detail


def test_trace_reads__no_object_key_or_storage_link_leaves_the_route(world):
    """Oracle: neither the list nor a granted detail carries an object key, the content
    prefix or a storage URL - content is served inline after the checks, nothing to follow."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    keys = {r.content_key for r in t.rows.inserted if r.content_key}
    for answer in (get(c, w.DEV_A, w.A), get(c, w.DEV_A, w.A, "/" + t.ids["granted"])):
        text = answer.text
        assert answer.status_code == 200 and "content_key" not in text
        assert not any(key in text for key in keys) and ship.CONTENT_PREFIX not in text
        assert "s3://" not in text and "http" not in text


# --- timing and pins -------------------------------------------------------------------------
def test_trace_reads__timing_is_measured_or_absent_never_zero():
    """Oracle: `elapsed_ms` is the admission-to-capture-finish interval of the row's own two
    instants (2 s here), and null - not 0 - when the capture never finished."""
    w = FakeWorld()
    t = traces(w)
    variant(t, "unfinished", "granted", completed_at=None)
    items = by_request(t, listed(client(w, t), w.DEV_A, w.A)["data"])
    assert items["granted"]["elapsed_ms"] == 2000
    assert items["unfinished"]["elapsed_ms"] is None


def test_trace_reads__pins_name_the_admitted_revision_and_trace_schema():
    """Oracle: every row names the rate card and price version it was admitted under and the
    trace schema version it was captured with, from the row itself."""
    w = FakeWorld()
    t = traces(w)
    variant(t, "repriced", "granted", price_version="pv9", request_schema_version=2)
    items = by_request(t, listed(client(w, t), w.DEV_A, w.A)["data"])
    pins = ("price_version", "request_schema_version", "rate_card_version")
    assert tuple(items["granted"].get(p) for p in pins) == ("pv1", 1, "rc1")
    assert tuple(items["repriced"].get(p) for p in pins) == ("pv9", 2, "rc1")


# --- filters ----------------------------------------------------------------------------------
def two_serving(w, t) -> TestClient:
    serving = Serving(w)
    serving.by_provider[w.A] = {FAKE_SERVING: w.MODELS[w.A], OTHER_SERVING: "marlin-7b"}
    app = FastAPI()
    lt.register(app, support.runtime(), lt.LabTraces(Sessions(w), w.access, serving, t.rows,
                                                     t.policy))
    return TestClient(app, raise_server_exceptions=False)


def test_trace_reads__filters_run_server_side_and_bind_the_cursor():
    """Oracle: a serving-version or model filter narrows what the projection is ASKED for (not
    a page filtered after the read); a filter outside the provider's own asks nothing; a cursor
    minted under one filter is refused under another and pages on under its own."""
    w = FakeWorld()
    t = traces(w)
    for n in range(3):
        variant(t, f"other{n}", "granted", serving_version_id=OTHER_SERVING,
                started_at=NOW - timedelta(seconds=30 + n))
    c = two_serving(w, t)
    first = listed(c, w.DEV_A, w.A, serving_version_id=OTHER_SERVING, limit=2)
    assert list(by_request(t, first["data"])) == ["other0", "other1"]
    assert t.rows.asked[-1] == ("page", (OTHER_SERVING,), None, 2)
    by_model = listed(c, w.DEV_A, w.A, model_id="marlin-7b", limit=2)
    assert by_model["data"] == first["data"]
    assert t.rows.asked[-1][1] == (OTHER_SERVING,)
    for other in ({}, {"model_id": "marlin-7b"}, {"serving_version_id": FAKE_SERVING}):
        answer = get(c, w.DEV_A, w.A, cursor=first["next_cursor"], **other)
        assert answer.json() == {"refusal": "invalid"}, other
    second = listed(c, w.DEV_A, w.A, serving_version_id=OTHER_SERVING, limit=2,
                    cursor=first["next_cursor"])
    assert list(by_request(t, second["data"])) == ["other2"] and second["next_cursor"] is None
    asked = len(t.rows.asked)
    assert listed(c, w.DEV_A, w.A, serving_version_id="5e000000-0000-4000-8000-0000000000ff") \
        == {"data": [], "next_cursor": None}
    assert len(t.rows.asked) == asked
    unfiltered = listed(c, w.DEV_A, w.A, limit=2)
    assert set(t.rows.asked[-1][1]) == {FAKE_SERVING, OTHER_SERVING}
    assert "." not in unfiltered["next_cursor"]
