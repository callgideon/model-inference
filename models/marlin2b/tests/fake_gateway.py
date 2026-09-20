#!/usr/bin/env python3
"""In-process fake gateway for bench.py: httpx.MockTransport, SSE chat completions,
the contracts-v1 upload handshake and scripted rejections. No network, no server.

    gw = FakeGateway(statuses={0: 429, 1: 402})
    async with httpx.AsyncClient(transport=gw.transport()) as c: ...

It is a test double for the client, not a model of the real gateway's behaviour.
"""
import asyncio, json, uuid

import httpx


class FakeGateway:
    def __init__(self, ttft=0.01, token_gap=0.001, tokens=3, usage=True, statuses=None,
                 require_bearer=None, server_timing=True, raise_with_key=None,
                 completion_tokens=None, role_chunk=True):
        self.ttft, self.token_gap, self.tokens, self.usage = ttft, token_gap, tokens, usage
        self.statuses = statuses or {}
        self.require_bearer, self.server_timing = require_bearer, server_timing
        self.raise_with_key, self.role_chunk = raise_with_key, role_chunk
        self.completion_tokens = completion_tokens
        self.seen = []            # one dict per chat request, for assertions
        self.uploads = {}

    def transport(self):
        return httpx.MockTransport(self)

    # -------------------------------------------------- handler

    async def __call__(self, request):
        path = request.url.path
        if request.method == "PUT":
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/uploads"):
            handle = f"up_{len(self.uploads):04d}"
            self.uploads[handle] = json.loads(request.content or b"{}")
            return httpx.Response(200, json={"handle": handle, "upload": {
                "method": "PUT", "url": "https://fake-upload.invalid/" + handle, "headers": {}}})
        if path.endswith("/complete"):
            return httpx.Response(200, json={"handle": path.split("/")[-2], "status": "ready"})
        if not path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": "no route", "code": "not_found"}})

        body = json.loads(request.content or b"{}")
        index = len(self.seen)
        auth = request.headers.get("authorization", "")
        self.seen.append({"index": index, "authorization_present": bool(auth),
                          "has_mm_processor_kwargs": "mm_processor_kwargs" in body,
                          "content": body.get("messages", [{}])[0].get("content"),
                          "max_tokens": body.get("max_tokens")})
        if self.raise_with_key:
            raise RuntimeError(f"upstream refused request with header Bearer {self.raise_with_key}")
        if self.require_bearer and auth != f"Bearer {self.require_bearer}":
            return self._error(401, "invalid_api_key", "incorrect api key provided")
        status = self.statuses.get(index, 200)
        if status != 200:
            return self._error(status, {429: "rate_limit_exceeded", 402: "insufficient_credit",
                                        400: "invalid_request", 503: "unavailable"}.get(status, "error"),
                               f"scripted status {status}")
        rid = uuid.uuid4().hex
        headers = {"content-type": "text/event-stream", "inference-id": rid}
        if self.server_timing:
            headers["server-timing"] = "queue;dur=12.5, prep;dur=340.0, gpu;dur=880.25"
        return httpx.Response(200, headers=headers, content=self._stream(rid, body))

    def _error(self, status, code, message):
        headers = {"retry-after": "3"} if status in (429, 503) else {}
        return httpx.Response(status, headers=headers,
                              json={"error": {"message": message, "code": code, "type": code,
                                              "request_id": uuid.uuid4().hex}})

    async def _stream(self, rid, body):
        def frame(obj):
            return f"data: {json.dumps(obj)}\n\n".encode()

        base = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk", "model": body.get("model")}
        if self.role_chunk:                      # role-only delta: carries no content, so no TTFT
            yield frame(base | {"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        await asyncio.sleep(self.ttft)
        for i in range(self.tokens):
            if i:
                await asyncio.sleep(self.token_gap)
            yield frame(base | {"choices": [{"index": 0, "delta": {"content": f"tok{i} "}}]})
        yield frame(base | {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if self.usage:
            yield frame(base | {"choices": [], "usage": {
                "prompt_tokens": 2061,
                "completion_tokens": self.completion_tokens if self.completion_tokens is not None
                else self.tokens * 7,
                "total_tokens": 2061 + (self.completion_tokens or self.tokens * 7)}})
        yield b"data: [DONE]\n\n"


def transport():
    """Entry point for `bench.py --dry-run-transport fake_gateway:transport`."""
    return FakeGateway(ttft=0.05, token_gap=0.002, tokens=6).transport()
