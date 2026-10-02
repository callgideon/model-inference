"""AP-02: opaque keyset cursors for the console API, HMAC-scoped to actor|operation|filters|sort.

Replaces `apps/app/lib/services/cursor.ts` (the App's server-side cursors) for the reads that
move behind FastAPI: the same shape (`<base64url payload>.<base64url mac>`, a 512-character
bound, a constant-time compare) with the scope widened from org to the server-derived actor and
the sort order (R270). A cursor from another actor, list, filter set or sort fails its MAC and is
`invalid_cursor`; the client only ever passes back what it was given.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping

from infrx.contracts import api, errors

MAX_CURSOR_CHARS = 512
MIN_SECRET_BYTES = 16


def scope(actor: api.Actor, operation: str, filters: Mapping[str, str | None], sort: str) -> str:
    """The query a cursor belongs to. Filters are sorted and an unset one is absent, so the same
    query has one scope whatever order its parameters arrived in."""
    who = f"{actor.audience}:{actor.user_id or ''}:{actor.org_id or ''}"
    entries = sorted((name, value) for name, value in filters.items() if value is not None)
    return f"{who}|{operation}|{json.dumps(entries, separators=(',', ':'))}|{sort}"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _mac(secret: bytes, scope_: str, payload: str) -> str:
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError(f"the cursor secret needs at least {MIN_SECRET_BYTES} bytes")
    return _b64(hmac.new(secret, f"{scope_}\0{payload}".encode(), hashlib.sha256).digest())


def encode(secret: bytes, scope_: str, key: tuple[str, ...]) -> str:
    payload = _b64(json.dumps(list(key), separators=(",", ":")).encode())
    return f"{payload}.{_mac(secret, scope_, payload)}"


def decode(secret: bytes, scope_: str, token: str) -> tuple[str, ...]:
    """The key this service minted for exactly this scope, or `InvalidCursor`."""
    if not 0 < len(token) <= MAX_CURSOR_CHARS:
        raise errors.InvalidCursor("cursor length")
    payload, dot, mac = token.partition(".")
    if not (payload and dot and mac):
        raise errors.InvalidCursor("cursor shape")
    if not hmac.compare_digest(mac.encode(), _mac(secret, scope_, payload).encode()):
        raise errors.InvalidCursor("cursor scope")
    try:
        key = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        raise errors.InvalidCursor("cursor payload") from None
    if not (isinstance(key, list) and key and all(isinstance(k, str) and k for k in key)):
        raise errors.InvalidCursor("cursor payload")
    return tuple(key)
