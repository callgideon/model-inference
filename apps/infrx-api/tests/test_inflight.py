#!/usr/bin/env python3
"""In-flight accounting: exactly one decrement per request, on every path.

A decrement that runs before the response is processed (or twice, via the outer
error handler) drifts `inflight` negative and MAX_INFLIGHT never fires again.

    python3 -m pytest apps/infrx-api/tests/test_inflight.py
    python3 apps/infrx-api/tests/test_inflight.py     # same checks, no pytest

No network: vLLM is an httpx.MockTransport.
"""
import json, os, sys

os.environ.setdefault("USAGE_LOG", "/tmp/gw-test-usage.jsonl")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from fastapi.testclient import TestClient

import gateway

BODY = {"model": "marlin2b", "messages": [{"role": "user", "content": "hi"}]}
OK = {"choices": [{"message": {"role": "assistant", "content": "hello"}}],
      "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


def fake_vllm(handler):
    """Point the gateway at a mock upstream; unauthenticated, whatever else ran first."""
    gateway.client = httpx.AsyncClient(base_url="http://vllm.local", transport=httpx.MockTransport(handler))
    gateway.LEGACY_KEY = gateway.SUPABASE_URL = ""
    gateway.inflight = 0


def post(body=BODY):
    with TestClient(gateway.app) as tc:
        return tc.post("/v1/chat/completions", json=body)


def test_non_streaming_success_releases_the_slot():
    fake_vllm(lambda req: httpx.Response(200, json=OK))
    assert post().status_code == 200
    assert gateway.inflight == 0, gateway.inflight


def test_unparseable_upstream_response_does_not_drift_the_counter():
    """`r.json()` raising used to decrement twice: once early, once in the handler."""
    fake_vllm(lambda req: httpx.Response(200, content=b"<html>not json</html>"))
    for _ in range(3):
        r = post()
        assert r.status_code == 502, r.text
        assert r.json()["error"]["type"] == "server_error", r.text
    assert gateway.inflight == 0, gateway.inflight


def test_upstream_error_still_releases_the_slot():
    def boom(req):
        raise httpx.ConnectError("vllm is down")

    fake_vllm(boom)
    assert post().status_code == 502
    assert gateway.inflight == 0, gateway.inflight


def test_limiter_still_fires_after_failures():
    """The point of the counter: 429 above MAX_INFLIGHT, forever, not until the
    first bad upstream response."""
    fake_vllm(lambda req: httpx.Response(200, content=b"not json"))
    for _ in range(5):
        assert post().status_code == 502
    assert gateway.inflight == 0, gateway.inflight

    gateway.inflight = gateway.MAX_INFLIGHT
    try:
        r = post()
        assert r.status_code == 429, r.text
    finally:
        gateway.inflight = 0


def test_streaming_decrements_exactly_once():
    def handler(req):
        chunks = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"]

        async def stream():
            for c in chunks:
                yield c

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())

    fake_vllm(handler)
    r = post(dict(BODY, stream=True))
    assert r.status_code == 200 and "[DONE]" in r.text, r.text
    assert gateway.inflight == 0, gateway.inflight


def test_streaming_non_200_upstream_releases_the_slot():
    fake_vllm(lambda req: httpx.Response(503, json={"error": "overloaded"}))
    assert post(dict(BODY, stream=True)).status_code == 200   # SSE body carries the upstream error
    assert gateway.inflight == 0, gateway.inflight


def test_streaming_failure_mid_stream_releases_the_slot():
    def handler(req):
        async def stream():
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            raise httpx.ReadError("upstream went away")

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())

    fake_vllm(handler)
    try:
        post(dict(BODY, stream=True))
    except Exception:
        pass                      # the generator raises through the ASGI app; that is fine
    assert gateway.inflight == 0, gateway.inflight


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
