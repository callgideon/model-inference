#!/usr/bin/env python3
"""AP-07 for AP-08: `infrx.traces.eligible.TraceEligible`, the read the judge's start job
samples from, on the lane's stack (key ap7: PostgreSQL 57559, ClickHouse 57560/57561, MinIO
57562; containers per `test_trace_stack.py`'s header).

    INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 uv run --frozen pytest -q tests/ap07/test_eligible.py

The rows are T2I's projection rows written with `ClickHouseProjection.insert` (each case sets
the one field it judges), except the last case, where the request goes the whole way: consent
through the data-use routes, the gateway's capture, the shipper. Grants are written through the
data-use routes as C1's owner (LAB-ACCESS's world: A owns `cc.MODEL`, served as `cc.SERVING`).
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.responses import JSONResponse

from infrx.contracts import errors
from infrx.gateway.routes import console_data_use as routes
from infrx.state.jobstore import connector
from infrx.traces import ship
from infrx.traces.eligible import TraceEligible
from infrx.traces.feedback import ClickHouseFeedbackProjection
from infrx.traces.retention import ClickHouseRetentionStore, Retention

from ..d import checks_admission as ca
from ..d import checks_credit as cc
from ..d import pgharness
from ..l.access.conftest import CASE
from ..t.capture.test_capture import ANSWER
from .test_data_use import client, grant_body, session
from .test_trace_stack import STACK, admitted, answered, bucket, clickhouse, consented  # noqa: F401
from .test_trace_stack import run, shipped_rows, stack  # noqa: F401

pytestmark = [pytest.mark.pg, pytest.mark.skipif(
    not STACK, reason="AP-07's stack needs INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 and its containers")]
BOTH_CONTENT = ["request_content", "response_content"]


def judging(s, *purposes: str, categories=BOTH_CONTENT, **extra) -> None:
    """C1's owner grants A `purposes` (default external_judging) on the model, via the route."""
    w = s.w
    owner = client(session(w.BOTH, w.C1))
    current = [g for g in owner.get(routes.GRANTS_PATH).json()["data"]
               if g["provider_org_id"] == w.A]
    answer = owner.post(routes.GRANTS_PATH, json=grant_body(
        w, purposes=list(purposes or ("external_judging",)), categories=categories,
        grant_version=current[0]["version"] if current else 0, **extra))
    assert answer.status_code in (200, 201), answer.text


def retention(s) -> Retention:
    projection = ship.ClickHouseProjection(s.ch)
    return Retention(ClickHouseRetentionStore(s.ch), projection,
                     ClickHouseFeedbackProjection(s.ch), None)


def reader(s) -> TraceEligible:
    return TraceEligible(connector(pgharness.dsn(CASE)), retention(s))


def row(s, request_id: str | None = None, **change) -> ship.TraceRow:
    """One full, stored trace of C1 on the model's serving version, a minute old (wall clock)."""
    at = datetime.now(timezone.utc) - timedelta(minutes=1)
    base = ship.TraceRow(
        org_id=s.w.C1, trace_id=f"seg-{uuid.uuid4().hex}:0", request_id=request_id
        or str(uuid.uuid4()), key_id=ca.C1_KEY, mode="full", started_at=at, completed_at=at,
        loss_reason="", content_complete=True, content_bytes=12, content_key=None,
        content_stored=True, request_schema_version=1, model_revision="marlin-ap7",
        price_version="ap7", serving_version_id=cc.SERVING, rate_card_version=None,
        policy_version=None)
    return dataclasses.replace(base, **change)


def projected(s, *rows: ship.TraceRow) -> list[str]:
    run(ship.ClickHouseProjection(s.ch).insert(list(rows)))
    return [r.request_id for r in rows]


def eligible(s, limit: int = 200) -> list[tuple[str, bool]]:
    return run(reader(s)(s.w.C1, cc.MODEL, limit))


def attach(s, request_id: str, mime: str) -> None:
    """A finalized source medium of `mime` on the admitted request (0003 job_media)."""
    w, handle = s.w, f"up-{uuid.uuid4().hex[:12]}"
    w.conn.execute(
        "insert into infrx.staged_media (org_id, handle, kind, state, digest, bytes, mime, "
        "storage_ref, finalized_at) values (%s, %s, 'upload', 'finalized', %s, 10, %s, "
        "'media/ap7', infrx.now())", (w.C1, handle, "sha256:" + "a" * 64, mime))
    w.conn.execute("insert into infrx.job_media (job_id, org_id, handle, role) values "
                   "(%s, %s, %s, 'source')", (request_id, w.C1, handle))


def test_eligible__only_full_stored_content_of_that_model_and_grantor_newest_first(stack):
    """Oracle: under a current judging grant, the grantor's full traces with stored content on
    the model's serving versions, newest first, bounded by `limit`; a minimal trace, one whose
    content was never stored, another organization's, another serving version's and one past
    T3's content bound are never offered; a request with two records is offered once."""
    s = stack
    judging(s)
    now = datetime.now(timezone.utc)
    newest, older = projected(s, row(s, started_at=now - timedelta(seconds=5)),
                              row(s, started_at=now - timedelta(minutes=5)))
    projected(s, row(s, newest, started_at=now - timedelta(seconds=6)))   # a second record
    projected(s, row(s, mode="minimal"), row(s, content_stored=False),
              row(s, org_id=s.w.C2), row(s, serving_version_id=str(uuid.uuid4())),
              row(s, started_at=now - timedelta(days=retention(s).content_days, minutes=1)))
    assert eligible(s) == [(newest, False), (older, False)]
    assert eligible(s, limit=1) == [(newest, False)]


def test_eligible__no_current_judging_grant_offers_nothing(stack):
    """Oracle: the grant is re-read on every call and must permit external_judging on BOTH
    content categories for this model: provider_sharing alone, request content alone, a
    revoked grant and an expired one each offer nothing."""
    s, w = stack, stack.w
    projected(s, row(s))
    assert eligible(s) == [], "the seed's provider_sharing grant opened judging"
    judging(s, "external_judging", categories=["request_content"])
    assert eligible(s) == [], "request content alone opened judging"
    judging(s)
    assert len(eligible(s)) == 1
    owner = client(session(w.BOTH, w.C1))
    [grant] = [g for g in owner.get(routes.GRANTS_PATH).json()["data"] if g["provider_org_id"] == w.A]
    assert owner.delete(f"{routes.GRANTS_PATH}/{grant['grant_id']}").status_code == 200
    assert eligible(s) == [], "a revoked grant still opened judging"
    judging(s, expires_at=(w.now() + timedelta(hours=1)).isoformat())
    assert len(eligible(s)) == 1
    w.advance(2 * 3600)
    assert eligible(s) == [], "an expired grant still opened judging"


def test_eligible__video_is_the_requests_admitted_video_source(stack):
    """Oracle: `has_video` is true exactly when the request was admitted with a video source -
    an image source, or no medium at all, is false."""
    s = stack
    on, _ = consented(s)
    judging(s)
    video, image, text = (admitted(s, on, f"ap7-{n}").request_id for n in ("v", "i", "t"))
    attach(s, video, "video/mp4")
    attach(s, image, "image/png")
    projected(s, *(row(s, r) for r in (video, image, text)))
    assert dict(eligible(s)) == {video: True, image: False, text: False}


def test_eligible__a_deleted_request_or_content_is_not_offered(stack):
    """Oracle: T3's tombstones are honoured - the owner's deletion takes the request out."""
    s = stack
    judging(s)
    kept, deleted = projected(s, row(s), row(s))
    run(retention(s).delete(s.w.C1, deleted, "owner"))
    assert [r for r, _ in eligible(s)] == [kept]


def test_eligible__the_bound_is_the_judges(stack):
    """Oracle: a limit outside 1..200 is refused, never a scan nobody chose."""
    s = stack
    for limit in (0, 201):
        with pytest.raises(errors.InvalidRequest):
            eligible(s, limit)


def test_eligible__a_captured_request_is_offered_end_to_end(stack):
    """Oracle (the composition): a request captured by the gateway on a consenting key and
    shipped is offered once judging is granted; the off key's request never is."""
    s = stack
    on, off = consented(s)
    judging(s, "provider_sharing", "external_judging")
    sync, quiet = admitted(s, on, "ap7-e2e"), admitted(s, off, "ap7-e2e-off")
    for request in (sync, quiet):
        answered(s, request, JSONResponse(ANSWER))
    assert shipped_rows(s) == 1
    assert eligible(s) == [(sync.request_id, False)]
