#!/usr/bin/env python3
"""D2 item 4 through the adapter: M3's `is_live`, W2's `put_result`/`read_result` and the
prepared prompt count, on the real store (`pgstore.factory`)."""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")


def test_is_live__non_terminal_only_and_unknown_is_not_live() -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")
    store = h.extra["store"]

    async def body():
        request = b.request(h)
        await h.port.admit(request, b.idem(request, "live"))
        assert await store.is_live(request.request_id) is True
        lease = await h.port.claim_preparation(request.request_id, "p")
        await h.port.prepared(lease, ())
        assert await store.is_live(request.request_id) is True, "a queued job is live"
        other = b.request(h)
        await h.port.admit(other, b.idem(other, "dies"))
        h.clock.advance(DEFAULTS.preparation_timeout_s)
        with pytest.raises(errors.AlreadyTerminal):
            await h.port.claim_preparation(other.request_id, "p")
        assert await store.is_live(other.request_id) is False, "a terminal job is live"
        assert await store.is_live(h.ids.uuid()) is False, "an unknown job is live"
        assert await store.is_live("not-a-uuid") is False
    asyncio.run(body())


def test_put_result__write_once_reference_and_owner_read() -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")
    store = h.extra["store"]

    async def body():
        request = b.request(h)
        await h.port.admit(request, b.idem(request, "r"))
        ref = await store.put_result(request.request_id, "an answer")
        assert ref == f"infrx-result:{request.request_id}"
        assert await store.put_result(request.request_id, "an answer") == ref
        with pytest.raises(errors.StateConflict):
            await store.put_result(request.request_id, "a different answer")
        with pytest.raises(errors.NotFound):
            await store.put_result(h.ids.uuid(), "orphan")
        assert await store.read_result(b.ORG_A, ref) == "an answer"
        with pytest.raises(errors.NotFound):
            await store.read_result(b.ORG_B, ref)
    asyncio.run(body())


def test_prepared__stores_the_prompt_count_within_the_ceiling() -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        request = b.request(h, max_input_tokens=1000)
        await h.port.admit(request, b.idem(request, "p"))
        lease = await h.port.claim_preparation(request.request_id, "p")
        with pytest.raises(errors.ContextLengthExceeded):
            await h.port.prepared(lease, (), prompt_tokens=1001)
        with pytest.raises(errors.InvalidRequest):
            await h.port.prepared(lease, (), prompt_tokens=True)
        queued = await h.port.prepared(lease, (), prompt_tokens=1000)
        assert queued.state.value == "queued"
    asyncio.run(body())
