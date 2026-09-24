#!/usr/bin/env python3
"""In-process fake gateway for bench.py: httpx.MockTransport, SSE chat completions,
the mounted upload contract (G4U, routes/uploads.py) and scripted rejections. No network,
no server.

    gw = FakeGateway(statuses={0: 429, 1: 402})
    async with httpx.AsyncClient(transport=gw.transport()) as c: ...

It is a test double for the client, not a model of the real gateway's behaviour.
"""
import asyncio, hashlib, json, re, uuid

import bench

# The double waits on the SAME clock the client does (E2 r1 review): with a virtual clock in
# `bench.SLEEP`, a test can make "the server took 250 ms" cost no real time and still be exact.
# Mixing a virtual client clock with real server sleeps leaves the clock free to jump while a
# request is in flight, which is what produced phantom scheduling lag.
def _sleep(seconds):
    return bench.SLEEP(seconds)

import httpx

# routes/uploads.py over media/uploads.py: the four constraint fields create takes, the
# media types the store accepts, and the digest spelling. test_upload_conformance holds
# every answer below to the mounted router's, probe by probe.
UPLOAD_CONSTRAINTS = frozenset({"max_bytes", "bytes", "accepted_mime", "digest"})
UPLOAD_MIME = frozenset({"video/mp4", "video/webm", "video/quicktime"})
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
HANDLE = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")


class FakeGateway:
    def __init__(self, ttft=0.01, token_gap=0.001, tokens=3, usage=True, statuses=None,
                 require_bearer=None, server_timing=True, raise_with_key=None,
                 completion_tokens=None, role_chunk=True, echo_key_in_code=None,
                 echo_key_in_long_body=None, echo_key_in_json_message=None, echo_pad=0,
                 escape_key=False, upload_url=None, put_status=200, truncate_stream=False,
                 finish_reason="stop", retry_after="3", chat_override=None, stream_error=None,
                 hostile_fields=None, extra_headers=None, idempotent=False, lose_ack=(),
                 bad_handle=False, bad_destination_ref=None, complete_override=None):
        self.ttft, self.token_gap, self.tokens, self.usage = ttft, token_gap, tokens, usage
        self.statuses = statuses or {}
        self.require_bearer, self.server_timing = require_bearer, server_timing
        self.raise_with_key, self.role_chunk = raise_with_key, role_chunk
        self.completion_tokens = completion_tokens
        # Hostile-server knobs: a key echoed into error.code or error.message, padded
        # with `echo_pad` so it STRADDLES the client's truncation point (120 for code,
        # 200 for message and for the non-JSON body) and optionally \uXXXX-escaped so a
        # scrub of the undecoded body cannot see it; a signed upload URL and a rejected
        # PUT; a 200 stream that stops without [DONE], finish_reason or usage.
        self.echo_key_in_code, self.echo_key_in_long_body = echo_key_in_code, echo_key_in_long_body
        self.echo_key_in_json_message = echo_key_in_json_message
        self.echo_pad, self.escape_key = echo_pad, escape_key
        # upload_url: a hostile create answer that also names a URL of its own (the shape the
        # pre-E1C client followed); the client must never send bytes or a bearer there.
        # Requests to any host but the first one seen land in `foreign`.
        self.upload_url, self.put_status = upload_url, put_status
        self.bad_destination_ref, self.complete_override = bad_destination_ref, complete_override
        self.home, self.foreign = None, []
        self.upload_calls = []    # (method, path) of every upload-route request
        self.truncate_stream, self.finish_reason = truncate_stream, finish_reason
        self.retry_after = retry_after
        # chat_override(request) -> a Response, or raises a transport exception: one knob
        # for the outcomes the leak matrix needs (redirects, odd error bodies, timeouts).
        # It runs after the request is recorded, so the upload handshake still works.
        # stream_error: emit this as an SSE `error` event mid-stream instead of finishing.
        self.chat_override, self.stream_error = chat_override, stream_error
        # hostile_fields: one string a server controls, planted in finish_reason, in the
        # usage counts and in the Inference-Id / Server-Timing / Retry-After headers at
        # once -- every field a client might copy into a row without thinking.
        self.hostile_fields, self.extra_headers = hostile_fields, extra_headers or {}
        # marlin-sop.md §3.1, the only part of idempotency a CLIENT can be tested against:
        # the store is keyed by (bearer, operation, Idempotency-Key) - org + operation +
        # key - and holds the canonical payload digest. Same key + same payload replays the
        # first outcome and creates NO second accepted item; same key + a different payload
        # is 409 idempotency_conflict. `lose_ack` accepts an item and then tears the
        # response, which is the interruption a resume has to survive without duplicating.
        # bad_handle: True for a too-short handle, or a literal handle string to plant one
        # of the grammar's edge cases (21 characters, or 22 containing '.' or '/').
        self.idempotent, self.lose_ack, self.bad_handle = idempotent, set(lose_ack), bad_handle
        self.idempotency = {}     # (bearer, key) -> payload digest
        self.accepted_keys = []   # one entry per item the server really created, in order
        self.seen = []            # one dict per chat request, for assertions
        self.uploads = {}

    def transport(self):
        return httpx.MockTransport(self)

    # -------------------------------------------------- handler

    async def __call__(self, request):
        path = request.url.path
        self.home = self.home or request.url.host
        if request.url.host != self.home:
            self.foreign.append((request.method, request.url.host, path,
                                 bool(request.headers.get("authorization"))))
            return httpx.Response(200, json={"ok": True})
        if "/uploads" in path:
            return self._upload(request, path)
        if not path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": "no route", "code": "not_found"}})

        body = json.loads(request.content or b"{}")
        index = len(self.seen)
        auth = request.headers.get("authorization", "")
        self.seen.append({"index": index, "authorization_present": bool(auth),
                          "has_mm_processor_kwargs": "mm_processor_kwargs" in body,
                          "has_stream_options": "stream_options" in body,
                          "content": body.get("messages", [{}])[0].get("content"),
                          "idempotency_key": request.headers.get("idempotency-key"),
                          "authorization": auth,
                          "max_tokens": body.get("max_tokens")})
        if self.chat_override:
            r = self.chat_override(request)
            if r is not None:
                return r
        if self.raise_with_key:
            raise RuntimeError(f"upstream refused request with header Bearer {self.raise_with_key}")
        if self.echo_key_in_code:            # the key lands in a field nothing scrubbed per-field
            return self._json_error_body(
                {"error": {"message": "nope", "type": "auth",
                           "code": "bad_key:" + "x" * self.echo_pad + self.echo_key_in_code}},
                self.echo_key_in_code)
        if self.echo_key_in_json_message:    # key straddling the 200-char message cut
            key = self.echo_key_in_json_message
            return self._json_error_body(
                {"error": {"message": "x" * self.echo_pad + key, "code": "auth", "type": "auth"}}, key)
        if self.echo_key_in_long_body:
            # Non-JSON body: 170 pad + 15-char lead-in puts the key at offset 185, so it
            # straddles the client's 200-character cut. A truncate-then-scrub client
            # leaves the first 15 characters of the key behind.
            return httpx.Response(401, text="x" * 170 + " rejected key: " +
                                  self.echo_key_in_long_body + " tail")
        if self.require_bearer and auth != f"Bearer {self.require_bearer}":
            return self._error(401, "invalid_api_key", "incorrect api key provided")
        status = self.statuses.get(index, 200)
        if status != 200:
            return self._error(status, {429: "rate_limit_exceeded", 402: "insufficient_credit",
                                        400: "invalid_request", 503: "unavailable"}.get(status, "error"),
                               f"scripted status {status}")
        key = request.headers.get("idempotency-key")
        digest = json.dumps(body, sort_keys=True)     # stands in for validate.payload_digest
        if self.idempotent and key:
            stored = self.idempotency.get((auth, key))
            if stored is not None:
                if stored != digest:
                    return self._error(409, "idempotency_conflict", "same key, other payload")
                rid = uuid.uuid4().hex                # replay: no second accepted item
                return httpx.Response(200, headers={"content-type": "text/event-stream",
                                                    "inference-id": rid,
                                                    "idempotency-replayed": "true"},
                                      content=self._stream(rid, body))
            self.idempotency[(auth, key)] = digest
            self.accepted_keys.append(key)
        if index in self.lose_ack:
            # The item is already accepted above; the client never learns that.
            raise httpx.ReadError("connection torn after the item was accepted")
        rid = uuid.uuid4().hex
        headers = {"content-type": "text/event-stream", "inference-id": rid}
        if self.server_timing:
            headers["server-timing"] = "queue;dur=12.5, prep;dur=340.0, gpu;dur=880.25"
        if self.hostile_fields:
            headers["inference-id"] = self.hostile_fields
            headers["retry-after"] = self.hostile_fields
            headers["server-timing"] = f"{self.hostile_fields};dur=1.0, queue;dur=2.5"
        headers.update(self.extra_headers)
        return httpx.Response(200, headers=headers, content=self._stream(rid, body))

    # -------------------------------------------------- uploads (routes/uploads.py)

    def _upload(self, request, path):
        self.upload_calls.append((request.method, path))
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) <= len("Bearer "):
            return self._error(401, "invalid_api_key", "no key")
        parts = path.rstrip("/").split("/")          # ['', 'v1', 'uploads', handle?, 'complete'?]
        if request.method == "POST" and parts[-1] == "uploads":
            return self._create(request, auth)
        handle = parts[3] if len(parts) > 3 else ""
        upload = self.uploads.get(handle)
        if not HANDLE.fullmatch(handle) or upload is None or upload["owner"] != auth:
            return self._error(404, "not_found", "not an upload handle")
        if request.method == "PUT" and len(parts) == 4:
            if self.put_status >= 400:     # the refusal quotes the planted URL, as a leak bait
                return httpx.Response(self.put_status, text=f"AccessDenied {self.upload_url}")
            mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
            if mime not in UPLOAD_MIME:
                return self._error(400, "unsupported_media", "the destination takes media")
            upload["data"], upload["mime"] = request.content, mime
            return httpx.Response(204)
        if request.method == "POST" and parts[-1] == "complete":
            return self._complete(request, handle, upload)
        return self._error(404, "not_found", "no route")

    def _create(self, request, auth):
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict) or set(body) - UPLOAD_CONSTRAINTS:
            return self._error(400, "invalid_request", "unknown upload constraints")
        mimes = body.get("accepted_mime", sorted(UPLOAD_MIME))
        if not isinstance(mimes, list) or not mimes or not set(mimes) <= UPLOAD_MIME \
                or ("digest" in body and not DIGEST.fullmatch(str(body["digest"]))):
            return self._error(400, "invalid_request", "bad constraint")
        # contracts/ids.py: `upl_` + 22..64 of [A-Za-z0-9_-]. A shorter handle is what
        # the client must refuse rather than send back as a reference.
        handle = (self.bad_handle if isinstance(self.bad_handle, str)
                  else "up_short" if self.bad_handle
                  else f"upl_{len(self.uploads):04d}" + "z" * 18)
        self.uploads[handle] = {"owner": auth, "constraints": body, "data": None,
                                "state": "created"}
        ticket = {"upload_handle": handle,
                  "destination_ref": self.bad_destination_ref or "infrx-upload:" + handle,
                  "max_bytes": body.get("max_bytes", 1 << 30), "accepted_mime": mimes,
                  "state": "created", "expires_at": "2026-09-27T12:00:00Z"}
        if self.upload_url:            # a URL the frozen UploadCreated does not carry
            ticket |= {"url": self.upload_url, "upload": {
                "method": "PUT", "url": self.upload_url,
                "headers": {"authorization": "Bearer planted"}}}
        return httpx.Response(201, json=ticket)

    def _complete(self, request, handle, upload):
        if request.content and json.loads(request.content) != {}:
            return self._error(400, "invalid_request", "completion takes no fields")
        data, want = upload["data"], upload["constraints"]
        if data is None:
            return self._error(400, "invalid_request", "no object was uploaded")
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if ("bytes" in want and len(data) != want["bytes"]) or \
                ("digest" in want and digest != want["digest"]):
            return self._error(400, "unsupported_media", "the bytes do not match")
        upload["state"] = "finalized"
        o = self.complete_override or {}
        return httpx.Response(200, json={
            "upload_handle": o.get("upload_handle", handle), "state": "finalized",
            "media": {"handle": handle, "kind": "upload",
                      "digest": o.get("media_digest", digest),
                      "bytes": o.get("media_bytes", len(data)), "mime": upload["mime"],
                      "duration_s": None}})

    def _json_error_body(self, obj, key):
        """Serialise by hand so the key can be \\uXXXX-escaped in the wire bytes: a client
        that scrubs the undecoded body misses it and json.loads hands back the real key."""
        text = json.dumps(obj)
        if self.escape_key:
            text = text.replace(key, "".join("\\u%04x" % ord(c) for c in key))
        return httpx.Response(401, content=text.encode(),
                              headers={"content-type": "application/json"})

    def _error(self, status, code, message):
        headers = {"retry-after": self.retry_after} if status in (429, 503) else {}
        return httpx.Response(status, headers=headers,
                              json={"error": {"message": message, "code": code, "type": code,
                                              "request_id": uuid.uuid4().hex}})

    async def _stream(self, rid, body):
        def frame(obj):
            return f"data: {json.dumps(obj)}\n\n".encode()

        base = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk", "model": body.get("model")}
        if self.role_chunk:                      # role-only delta: carries no content, so no TTFT
            yield frame(base | {"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        await _sleep(self.ttft)
        for i in range(self.tokens):
            if i:
                await _sleep(self.token_gap)
            yield frame(base | {"choices": [{"index": 0, "delta": {"content": f"tok{i} "}}]})
        if self.stream_error:         # 200 headers, then an error event inside the stream
            yield frame({"error": self.stream_error})
            return
        if self.hostile_fields:       # finish_reason and usage are server-controlled too
            yield frame(base | {"choices": [{"index": 0, "delta": {},
                                             "finish_reason": self.hostile_fields}],
                                "usage": {"prompt_tokens": self.hostile_fields,
                                          "completion_tokens": self.hostile_fields}})
            yield b"data: [DONE]\n\n"
            return
        if self.truncate_stream:      # 200 + content, then the connection just ends
            return
        if self.finish_reason:
            yield frame(base | {"choices": [{"index": 0, "delta": {}, "finish_reason": self.finish_reason}]})
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
