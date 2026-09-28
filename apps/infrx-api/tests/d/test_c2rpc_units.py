#!/usr/bin/env python3
"""C2-RPC: the Python half of `PgContentRefs` / `PgSampleRestrictions`, with NO database - what
they send to the RPCs of `0041_lab_content_refs.sql` and what they hand back.
`code_mutants_c2rpc.py`'s Python list runs here; the SQL is `test_c2rpc_content.py`.

`RefBinding` is the content lane's (`infrx.content`, merging beside this lane); on a tree
without it, `ensure_ref_binding` registers a stand-in with its exact fields so these cases
run, and the real model is used wherever it exists.

    uv run --frozen pytest -q tests/d/test_c2rpc_units.py
"""
from __future__ import annotations

import sys
import types
from datetime import UTC, datetime

from infrx.state.lab_content import PgContentRefs, PgSampleRestrictions

from .test_adapter_units import _Conn
from .test_d7_units import NEMO, _ok, _sent

BINDING = {"grant_id": "90000000-0000-4000-8000-000000000001", "grant_version": 2,
           "grantor_org_id": "a0000000-0000-4000-8000-000000000001",
           "request_id": "c2000000-0000-4000-8000-000000000001", "purpose": "training",
           "expires_at": "2026-09-28T12:05:00+00:00"}


def ensure_ref_binding() -> None:
    try:
        import infrx.content  # noqa: F401
    except ImportError:
        from pydantic import BaseModel, ConfigDict, Field
        module = types.ModuleType("infrx.content")

        class RefBinding(BaseModel):
            model_config = ConfigDict(frozen=True, extra="forbid")
            grant_id: str
            grant_version: int = Field(ge=1)
            grantor_org_id: str
            request_id: str
            purpose: str
            expires_at: datetime
        module.RefBinding = RefBinding
        sys.modules["infrx.content"] = module


def _store(cls, *answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return cls(connect), conn


def test_refs__send_the_binding_inputs_and_hand_back_a_typed_binding() -> None:
    ensure_ref_binding()
    store, conn = _store(PgContentRefs, BINDING, BINDING, {"held": True}, {"held": False})
    got = _ok(store.issue(handle_sha256="ab" * 32, user_id="u", provider_org_id=NEMO,
                          grant_ref="g", request_id="r", purpose="training",
                          categories=("request_content", "response_content"), ttl_s=300))
    assert type(got).__name__ == "RefBinding", got
    assert (got.grant_version, got.expires_at.tzinfo is not None) == (2, True), got
    assert _ok(store.redeem(handle_sha256="ab" * 32, user_id="u", provider_org_id=NEMO)) == got
    assert _ok(store.held("o", "r")) is True and _ok(store.held("o", "r2")) is False
    assert [_sent(conn, n) for n in range(4)] == [
        ("lab_content_ref_issue", {"handle_sha256": "ab" * 32, "user_id": "u",
                                   "provider_org_id": NEMO, "grant_ref": "g",
                                   "request_id": "r", "purpose": "training",
                                   "categories": ["request_content", "response_content"],
                                   "ttl_s": 300}),
        ("lab_content_ref_redeem", {"handle_sha256": "ab" * 32, "user_id": "u",
                                    "provider_org_id": NEMO}),
        ("lab_content_ref_held", {"org_id": "o", "request_id": "r"}),
        ("lab_content_ref_held", {"org_id": "o", "request_id": "r2"})]


def test_restrictions__send_the_provider_ids_reason_and_bounds() -> None:
    store, conn = _store(PgSampleRestrictions, {"tombstoned": ["s1"]}, {"bounded": 1},
                         {"s1": "grant_revoked"}, ["s2"])
    at = datetime(2026, 9, 28, 12, tzinfo=UTC)
    assert _ok(store.tombstone(("s1",), provider_org_id=NEMO, reason="grant_revoked")) == ["s1"]
    assert _ok(store.bound({"s2": at}, provider_org_id=NEMO)) is None
    assert _ok(store.blocked("d", provider_org_id=NEMO)) == {"s1": "grant_revoked"}
    assert _ok(store.permitted("d", provider_org_id=NEMO, purpose="training")) == ["s2"]
    assert [_sent(conn, n) for n in range(4)] == [
        ("lab_tombstone_samples", {"provider_org_id": NEMO, "sample_ids": ["s1"],
                                   "reason": "grant_revoked"}),
        ("lab_bound_samples", {"provider_org_id": NEMO, "bounds": [
            {"sample_id": "s2", "content_until": "2026-09-28T12:00:00+00:00"}]}),
        ("lab_blocked_samples", {"provider_org_id": NEMO, "dataset_ref": "d"}),
        ("lab_permitted_samples", {"provider_org_id": NEMO, "dataset_ref": "d",
                                   "purpose": "training"})]
