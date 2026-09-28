#!/usr/bin/env python3
"""WR-B3-2: `POST /lab/v1/checkpoints` - CHECKPOINT-IDEM, the route half.

    uv run --frozen pytest -q tests/g/lab_checkpoints

B3's `receive` over its fake world (`tests/b/checkpoints/world.py`: B1's D7 fake with 0029's
receipts and outbox, the ledger lab-sql persists as 0042, keys generated per world). The route
adds only the raw-bytes seam, the signature header, the body bound, the key directory and
the Lab's fixed refusals; B3's own rules are B3's cases.

Failure oracles: a route mounted without its switch; a body re-encoded before the HMAC (a
signature over the caller's exact bytes would fail) or a signature taken from anywhere but
`X-Infrx-Signature`; an unsigned, forged or foreign event recorded; an unbounded body; a key
directory that accepts a short secret or a key without a provider, or echoes a secret.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.gateway.routes import lab_checkpoints as lc

from .. import support
from ...b.checkpoints.world import NEMO, OTHER, World


def mounted(world: World | None = None):
    rt = SimpleNamespace(settings=support.settings(), clock=time.time)
    app = FastAPI()
    if world is not None:
        rt.lab_checkpoints = lc.LabCheckpoints(world.keys.get, world.ledger, world.store)
    return app, lc.register(app, rt), TestClient(app)


def post(client, body: bytes, signature: str | None):
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["x-infrx-signature"] = signature
    return client.post(lc.CHECKPOINTS_PATH, content=body, headers=headers)


def test_lab_checkpoints__nothing_is_mounted_without_the_switch():
    """LAB_CHECKPOINTS off (no `rt.lab_checkpoints`): no route; on, the receiver only."""
    app, x, client = mounted()
    assert x is None and client.post(lc.CHECKPOINTS_PATH, content=b"{}").status_code == 404
    world = World()
    app, x, _ = mounted(world)
    assert x is not None and [r.path for r in app.routes if r.path == lc.CHECKPOINTS_PATH] \
        == [lc.CHECKPOINTS_PATH]


def test_lab_checkpoints__a_signed_event_is_one_receipt_and_a_replay_the_same():
    """The signed body is B3's receipt (202) and ONE `checkpoint_received` event; the same
    request again is the same receipt and no second event. The HMAC is over the caller's
    exact bytes: a body with its own spacing verifies as sent."""
    world = World()
    _, _, client = mounted(world)
    event = world.event(1)
    raw = b"  " + world.body(event)[0].replace(b": ", b":   ") + b"\n"
    signature = lc.checkpoints.sign(raw, world.secret)
    first = post(client, raw, signature)
    assert first.status_code == 202, first.text
    assert first.json()["state"] == "received"
    assert first.json()["checkpoint_id"] == event["checkpoint_id"]
    again = post(client, raw, signature)
    assert again.status_code == 202 and again.json() == first.json()
    assert len(world.store.outbox) == 1 and list(world.ledger.by_id) == [event["checkpoint_id"]]


def test_lab_checkpoints__unsigned_forged_and_foreign_events_are_refused_unrecorded():
    """No signature, a signature over other bytes, a key of another provider signing for
    this one: 401 `unauthenticated`, nothing recorded. The signature is the header's only."""
    world = World()
    _, _, client = mounted(world)
    body, signature = world.body(world.event(1))
    refusals = [post(client, body, None),
                post(client, body.replace(b'"step": 1', b'"step": 9'), signature),
                post(client, *world.body({**world.event(1), "key_id": "key-other"},
                                         world.other_secret))]
    assert [r.status_code for r in refusals] == [401, 401, 401]
    assert all(r.json() == {"refusal": "unauthenticated"} for r in refusals)
    smuggled = client.post(lc.CHECKPOINTS_PATH + f"?signature={signature}", content=body,
                           headers={"content-type": "application/json",
                                    "signature": signature})
    assert smuggled.status_code == 401
    assert world.store.outbox == [] and world.ledger.by_id == {}
    assert OTHER != NEMO


def test_lab_checkpoints__an_oversized_body_is_refused_before_b3():
    """One event is small: a body past the bound is refused (422) before any verification."""
    world = World()
    _, _, client = mounted(world)
    body = b'{"pad": "' + b"x" * lc.MAX_BODY_BYTES + b'"}'
    answer = post(client, body, lc.checkpoints.sign(body, world.secret))
    assert answer.status_code == 422 and answer.json() == {"refusal": "invalid"}
    assert world.ledger.by_id == {}


def test_lab_checkpoints__the_key_directory_binds_each_key_to_a_provider_and_a_secret():
    """`LAB_CHECKPOINT_KEYS`: key id -> (provider, secret bytes). A key without a provider
    uuid, with a short secret, or a directory that is not one refuses - naming the setting,
    never echoing a secret."""
    secret = "ab" * 32
    keys = lc.key_directory('{"k1": {"provider_org_id": "%s", "secret": "%s"}}'
                            % (NEMO, secret))
    assert keys("k1") == (NEMO, bytes.fromhex(secret)) and keys("k2") is None
    for bad in ('{"k1": {"provider_org_id": "nemo", "secret": "%s"}}' % secret,
                '{"k1": {"provider_org_id": "%s", "secret": "%s"}}' % (NEMO, "ab" * 31),
                '{"k1": {"secret": "%s"}}' % secret, "[]", "{}", "not json"):
        with pytest.raises(ValueError) as refused:
            lc.key_directory(bad)
        assert "LAB_CHECKPOINT_KEYS" in str(refused.value) and secret not in str(refused.value)
