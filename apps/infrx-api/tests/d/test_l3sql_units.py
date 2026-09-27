#!/usr/bin/env python3
"""L3-SQL: the Python half of `PgLabControlStore`, with NO database - what it sends to the
RPCs of `0032_lab_control.sql`. `code_mutants_l3sql.py`'s Python list runs here; the SQL is
`test_l3sql_control.py`.

    uv run --frozen pytest -q tests/d/test_l3sql_units.py
"""
from __future__ import annotations

from infrx.state.lab_control import PgLabControlStore

from .test_adapter_units import _Conn
from .test_d7_units import NEMO, _ok, _sent


def _store(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgLabControlStore(connect), conn


def test_calls__carry_the_provider_and_the_operator_marker_only_where_it_belongs() -> None:
    store, conn = _store(*({},) * 6)
    _ok(store.move("d", "validating", provider_org_id=NEMO, actor="dev", reason="r"))
    _ok(store.move("d", "draining", provider_org_id=NEMO, actor="ops", reason="r",
                   operator=True))
    _ok(store.approve("p", provider_org_id=NEMO, public_model_id="m", rate_card_version="c",
                      actor="ops", reason="r"))
    _ok(store.rollback("m", "p", actor="ops", reason="r"))
    _ok(store.open_dev_wallet(NEMO))
    _ok(store.history(NEMO, "d"))
    move = {"provider_org_id": NEMO, "deployment_revision_id": "d"}
    assert [_sent(conn, n) for n in range(6)] == [
        ("lab_move_deployment", {**move, "state": "validating", "actor": "dev",
                                 "operator": False, "reason": "r"}),
        ("lab_move_deployment", {**move, "state": "draining", "actor": "ops",
                                 "operator": True, "reason": "r"}),
        ("lab_approve_publication", {"provider_org_id": NEMO, "deployment_revision_id": "p",
                                     "public_model_id": "m", "rate_card_version": "c",
                                     "actor": "ops", "operator": True, "reason": "r"}),
        ("lab_rollback_publication", {"public_model_id": "m", "deployment_revision_id": "p",
                                      "actor": "ops", "operator": True, "reason": "r"}),
        ("lab_open_dev_wallet", {"provider_org_id": NEMO}),
        ("lab_control_history", move)]
