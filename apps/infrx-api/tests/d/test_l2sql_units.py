#!/usr/bin/env python3
"""L2-SQL: the Python half of `PgAccessStore`, with NO database - what it sends to the
RPCs and how it shapes their rows into the frozen v2 records. `code_mutants_l2sql.py`'s
Python list runs here; the SQL is `test_l2sql_access.py`.

    uv run --frozen pytest -q tests/d/test_l2sql_units.py
"""
from __future__ import annotations

import asyncio

from infrx.contracts import errors
from infrx.state.lab_access import PgAccessStore

from .test_adapter_units import _Conn, _db_error, _refused

NEMO, OTHER = "b0000001-0000-4000-8000-000000000001", "b0000009-0000-4000-8000-000000000009"
USER, ORG = "c1000000-0000-4000-8000-000000000006", "0a000000-0000-4000-8000-00000000000a"


def _store(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgAccessStore(connect), conn


def _args(conn, n: int = 0) -> dict:
    return conn.sent[n][1][0].obj


def _ok(coro):
    """Any exception is the assertion it stands for (R40: a mutant dies on an assertion)."""
    try:
        return asyncio.run(coro)
    except Exception as failed:
        raise AssertionError(f"{type(failed).__name__}: {failed}") from None


def _member(provider, granted, revoked=None) -> dict:
    return {"provider_org_id": provider, "provider_name": "NemoStation", "user_id": USER,
            "role": "developer", "granted_by": "ops", "granted_at": granted,
            "revoked_at": revoked}


def _grant(version, revoked=None) -> dict:
    return {"grant_id": "90000000-0000-4000-8000-000000000001", "version": version,
            "grantor_org_id": ORG, "recipient_provider_org_id": NEMO, "model_ids": ["m"],
            "categories": ["request_content"], "purposes": ["training"],
            "retention_days": 30, "effective_at": "2026-09-27T00:00:00Z", "expires_at": None,
            "revoked_at": revoked}


def test_memberships__the_users_rows_without_the_display_name_latest_per_provider() -> None:
    rows = [_member(NEMO, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"),
            _member(OTHER, "2026-09-03T00:00:00Z"), _member(NEMO, "2026-09-04T00:00:00Z")]
    store, conn = _store(rows, rows, rows)
    got = _ok(store.memberships_for_user(USER))
    assert _args(conn) == {"user_id": USER} and "infrx.lab_provider_memberships" in conn.sent[0][0]
    assert [m.provider_org_id for m in got] == [NEMO, OTHER, NEMO]
    current = _ok(store.membership(NEMO, USER))
    assert current is not None and current.revoked_at is None, current
    assert _ok(store.membership("9e000000-0000-4000-8000-00000000009e", USER)) is None


def test_grants__history_of_the_pair_and_the_latest_is_current() -> None:
    history = [_grant(1), _grant(2, "2026-09-28T00:00:00Z")]
    store, conn = _store(history, history, [])
    assert [g.version for g in _ok(store.grant_history(ORG, NEMO))] == [1, 2]
    assert _args(conn) == {"grantor_org_id": ORG, "recipient_provider_org_id": NEMO}
    current = _ok(store.current_grant(ORG, NEMO))
    assert current.version == 2 and current.revoked_at is not None, "not the latest version"
    assert _ok(store.current_grant(ORG, OTHER)) is None


def test_writes__carry_the_actor_and_refusals_are_typed() -> None:
    store, conn = _store(_grant(1), _grant(2, "2026-09-28T00:00:00Z"),
                         _db_error("P0001", "forbidden: only the owner"))
    _ok(store.put_grant(USER, {"grantor_org_id": ORG, "recipient_provider_org_id": NEMO}))
    assert _args(conn, 0) == {"grantor_org_id": ORG, "recipient_provider_org_id": NEMO,
                              "actor_user_id": USER}
    _ok(store.revoke_grant(USER, ORG, NEMO))
    assert _args(conn, 1) == {"actor_user_id": USER, "grantor_org_id": ORG,
                              "recipient_provider_org_id": NEMO}
    _refused(errors.Forbidden, store.revoke_grant(USER, ORG, NEMO))


def test_aggregates__are_the_providers_rows() -> None:
    store, conn = _store([{"deployment_revision_id": "r", "requests": 1}])
    assert _ok(store.deployment_aggregates(NEMO)) == [{"deployment_revision_id": "r",
                                                       "requests": 1}]
    assert _args(conn) == {"provider_org_id": NEMO}
