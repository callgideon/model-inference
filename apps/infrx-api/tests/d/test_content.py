#!/usr/bin/env python3
"""D10.b on real PostgreSQL (both images): the content lifecycle checks of
`checks_content.py` on the "admission" scenario and the attach/delete and claim/claim races.

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_content.py
    INFRX_D_TASK=d10 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_content.py
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_content as ck
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_content"
_state: dict = {}


def _db():
    if "conn" not in _state:
        pgharness.ensure()
        pgharness.recreate(DB)
        pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(DB)
        ca.seed_admission(conn)
        _state["conn"] = conn
    return _state["conn"]


def test_content_liveness() -> None:
    print(ck.check_content_liveness(_db()))


def test_content_protocol() -> None:
    print(ck.check_content_protocol(_db()))


def test_content_scrub() -> None:
    print(ck.check_content_scrub(_db()))


def test_a_pre_0018_success_without_expiry_still_scrubs() -> None:
    print(ck.check_legacy_success_scrub(_db()))


def test_content_privileges() -> None:
    print(ck.check_content_privileges(_db()))


def test_content_races() -> None:
    _db()
    print(ck.check_content_races(pgharness.connect, DB))
