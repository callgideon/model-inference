"""G2-FIX F-1: the Q harness never adopts a Valkey it does not own (R63).

Failure oracle: before G2-FIX, `ensure()` saw *any* listener on its port and marked it
started, so `kill_and_restart()` later targeted a container it never created (G-GATES
F-1: D2's `infrx-e2c-valkey` on the shared e2c port, `docker kill infrx-q3-valkey-55493:
No such container`). These cases need no Docker: the listener is a plain socket.
"""
from __future__ import annotations

import socket
import subprocess
import uuid

import pytest

from . import vkharness


@pytest.fixture
def foreign(monkeypatch):
    """A listener on the harness port that is not the harness's container."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        monkeypatch.setattr(vkharness, "PORT", port)
        monkeypatch.setattr(vkharness, "CONTAINER", f"infrx-q3-valkey-g2fix-{uuid.uuid4().hex}")
        monkeypatch.setattr(vkharness, "_started", False)
        yield port


def test_q_harness__a_listener_it_did_not_start_is_refused_not_adopted(foreign):
    with pytest.raises(RuntimeError, match=rf"127\.0\.0\.1:{foreign} .*{vkharness.CONTAINER}"):
        vkharness.ensure()
    assert vkharness._started is False


def test_q_harness__the_refusal_names_the_foreign_container(foreign, monkeypatch):
    def docker(*args, check=True):
        out = "infrx-e2c-valkey\n" if args[:1] == ("ps",) else ""
        return subprocess.CompletedProcess(args, 0 if args[:1] == ("ps",) else 1, out, "")
    monkeypatch.setattr(vkharness, "_docker", docker)
    with pytest.raises(RuntimeError, match="infrx-e2c-valkey"):
        vkharness.ensure()
