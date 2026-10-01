"""AP-02 cursors (`infrx.console.cursor`): opaque, HMAC-scoped to actor|operation|filters|sort.

Failure oracles: a cursor minted for one actor, list, filter set or sort order must not resume
another; a disturbed character must not verify; an oversize or malformed cursor is refused
before any decoding; the filter order and an unset filter do not change the scope.
"""
from __future__ import annotations

import pytest

from infrx.console import cursor
from infrx.contracts import api, errors

SECRET = b"ap02-test-cursor-secret"
ME = api.Actor(audience="session", user_id="u1", org_id="o1")
OTHER = api.Actor(audience="session", user_id="u2", org_id="o2")


def _scope(actor=ME, operation="requests", filters=None, sort="created_at.desc"):
    return cursor.scope(actor, operation, filters or {}, sort)


@pytest.mark.parametrize("key", [("a",), ("ab",), ("abc",), ("2026-09-20 11:00:00+00", "b1")],
                         ids=["pad2", "pad0", "pad1", "pair"])
def test_cursor__round_trips_its_key_for_the_same_scope(key):
    """Every base64 padding residue, and a two-part keyset."""
    token = cursor.encode(SECRET, _scope(), key)
    assert cursor.decode(SECRET, _scope(), token) == key


@pytest.mark.parametrize("other", [
    _scope(actor=OTHER),
    _scope(operation="credit-ledger"),
    _scope(filters={"model": "nemostation/marlin-2b"}),
    _scope(sort="created_at.asc"),
], ids=["actor", "operation", "filters", "sort"])
def test_cursor__from_another_scope_is_invalid(other):
    token = cursor.encode(SECRET, _scope(), ("k",))
    with pytest.raises(errors.InvalidCursor):
        cursor.decode(SECRET, other, token)


def test_cursor__scope_ignores_filter_order_and_unset_filters():
    a = _scope(filters={"model": "m", "key_id": "k", "from": None})
    b = _scope(filters={"key_id": "k", "model": "m"})
    assert a == b


def test_cursor__a_disturbed_or_foreign_cursor_is_invalid():
    token = cursor.encode(SECRET, _scope(), ("k",))
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    for bad in (flipped, token + "x", "x" * 600, "", "nodot", f".{token}", f"{token}.",
                cursor.encode(b"another-secret-of-length", _scope(), ("k",)),
                cursor.encode(SECRET, _scope(), ("k" * 600,))):     # authentic but oversize
        with pytest.raises(errors.InvalidCursor):
            cursor.decode(SECRET, _scope(), bad)


def test_cursor__a_short_secret_is_refused():
    with pytest.raises(ValueError):
        cursor.encode(b"short", _scope(), ("k",))
