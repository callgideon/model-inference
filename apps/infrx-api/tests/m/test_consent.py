#!/usr/bin/env python3
"""M3: trace/judge reuse of owned media under independent consent (TRACE-TENANT).

    uv run --frozen pytest -q tests/m/test_consent.py
"""
from __future__ import annotations

import base64
from datetime import timedelta

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.records import ConsentSnapshot, TraceMode
from infrx.media.consent import read_for_reuse

from .test_uploads import CLIP, adapter_for, finalized, run

RETENTION_DAYS = 30


def consent(org_id=b.ORG_A, **changes):
    fields = dict(org_id=org_id, consent_version=1, trace_mode=TraceMode.full,
                  content_retention_days=RETENTION_DAYS, evaluation_consent=True,
                  effective_at=DEFAULT_START - timedelta(days=1))
    return ConsentSnapshot(**(fields | changes))


def reuse(adapter, handle, snapshot, *, purpose="trace", org_id=b.ORG_A, after=timedelta(0)):
    captured = adapter.clock.now()
    return run(read_for_reuse(adapter, org_id, handle, snapshot, purpose=purpose,
                              captured_at=captured, now=captured + after))


@pytest.mark.parametrize("purpose", ["trace", "judge"])
def test_consented_reuse_returns_the_verified_bytes(purpose):
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    assert reuse(adapter, handle, consent(), purpose=purpose) == (ref, CLIP)


def test_another_orgs_consent_never_widens_the_tenant():
    """TRACE-TENANT: org B's consent cannot open org A's media, nor A's consent B's."""
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    with pytest.raises(errors.NotFound):
        reuse(adapter, handle, consent(b.ORG_B))                  # A's media, B's consent
    with pytest.raises(errors.NotFound):
        reuse(adapter, handle, consent(b.ORG_B), org_id=b.ORG_B)  # B asking for A's handle


@pytest.mark.parametrize("changes", [
    {"trace_mode": TraceMode.minimal}, {"trace_mode": TraceMode.off},
    {"revoked_at": "2026-09-20T11:00:00Z"}, {"effective_at": "2026-09-21T00:00:00Z"},
], ids=["minimal", "off", "revoked", "not-yet-effective"])
def test_trace_reuse_needs_current_full_trace_consent(changes):
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    with pytest.raises(errors.ConsentMissing):
        reuse(adapter, handle, consent(**changes))


def test_trace_consent_is_not_evaluation_consent():
    """Keeping content in a trace is not agreeing to send it to a judge."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    snapshot = consent(evaluation_consent=False)
    assert reuse(adapter, handle, snapshot)[0] == ref
    with pytest.raises(errors.ConsentMissing):
        reuse(adapter, handle, snapshot, purpose="judge")


def test_evaluation_consent_is_not_retroactive():
    """R56: content captured before the consent took effect is not judged."""
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    snapshot = consent(effective_at=adapter.clock.now() + timedelta(seconds=1))
    with pytest.raises(errors.ConsentMissing):
        reuse(adapter, handle, snapshot, purpose="judge", after=timedelta(seconds=2))


def test_content_expires_logically_at_its_retention():
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    limit = timedelta(days=RETENTION_DAYS)
    assert reuse(adapter, handle, consent(), after=limit - timedelta(seconds=1))[0] == ref
    with pytest.raises(errors.ResultExpired):
        reuse(adapter, handle, consent(), after=limit)


@pytest.mark.parametrize("change", ["replaced", "deleted"])
def test_the_bytes_returned_are_the_refs_bytes(change):
    """A materialized (non-upload) ref, whose object `resolve_owned` does not re-read:
    the resolver hashes what it hands T or J."""
    adapter = adapter_for()
    ref = run(adapter.materialize(b.ORG_A, "data:video/mp4;base64,"
                                  + base64.b64encode(CLIP).decode()))
    if change == "replaced":
        adapter.objects.seed(ref.storage_ref, b"other bytes")
    else:
        run(adapter.objects.delete(ref.storage_ref))
    with pytest.raises(errors.NotFound):
        reuse(adapter, ref.handle, consent())


def test_an_unknown_purpose_is_refused():
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    with pytest.raises(errors.InvalidRequest):
        reuse(adapter, handle, consent(), purpose="export")
