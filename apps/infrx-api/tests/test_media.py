#!/usr/bin/env python3
"""Media fetch: SSRF address validation, the streaming size cap, redirect
re-validation, and that vLLM is handed a data: URL instead of the caller's URL.

    python3 -m pytest apps/infrx-api/tests/test_media.py
    python3 apps/infrx-api/tests/test_media.py     # same checks, no pytest

No network: DNS is stubbed, the fetch client and vLLM are httpx.MockTransport,
and ffprobe is stubbed (the test bytes are not a real video).
"""
import asyncio, base64, os, sys

os.environ.setdefault("USAGE_LOG", "/tmp/gw-test-usage.jsonl")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import gateway

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"payload" * 10


def fake_dns(mapping):
    """gateway.resolve_public against a host -> addresses table; records lookups."""
    seen = []

    async def resolve(host):
        seen.append(host)
        addrs = mapping.get(host)
        if addrs is None and host.replace(".", "").isdigit():
            addrs = [host]          # getaddrinfo resolves an IP literal to itself
        if addrs is None:
            return None, "dns"
        if not all(gateway.address_allowed(a) for a in addrs):
            return None, "blocked-address"
        return addrs, None

    gateway.resolve_public = resolve
    return seen


def fake_fetch(handler):
    gateway.fetch_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                     follow_redirects=False)


def test_address_allowed():
    for bad in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                "169.254.1.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "100.64.0.1",
                "::1", "fe80::1", "fc00::1", "::ffff:169.254.169.254", "::", "not-an-ip"]:
        assert not gateway.address_allowed(bad), bad
    for good in ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"]:
        assert gateway.address_allowed(good), good


def test_blocked_and_dns_classes():
    async def go():
        fake_dns({"metadata.evil.test": ["169.254.169.254"], "ok.test": ["93.184.216.34"]})
        fake_fetch(lambda req: httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"}))
        with open("/dev/null", "wb") as f:
            assert (await gateway.fetch_video("http://metadata.evil.test/latest/meta-data/", f))[1] == "blocked-address"
            assert (await gateway.fetch_video("https://nxdomain.test/a.mp4", f))[1] == "dns"
            assert (await gateway.fetch_video("file:///etc/passwd", f))[1] == "unsupported-scheme"
            assert (await gateway.fetch_video("https://ok.test/a.mp4", f)) == ("video/mp4", None)
    asyncio.run(go())


def test_streaming_size_cap(tmp=None):
    async def go():
        fake_dns({"big.test": ["93.184.216.34"]})
        chunk, sent = b"x" * 65536, []

        def handler(req):
            async def stream():
                for _ in range(200):              # 13 MB if it is ever fully read
                    sent.append(1)
                    yield chunk
            return httpx.Response(200, headers={"content-type": "video/mp4"}, content=stream())

        fake_fetch(handler)
        gateway.MAX_VIDEO_MB = 0.25
        path = "/tmp/gw-test-cap.bin"
        with open(path, "wb") as f:
            assert (await gateway.fetch_video("https://big.test/a.mp4", f))[1] == "too-large"
        assert os.path.getsize(path) <= 0.25 * 2**20 + len(chunk), os.path.getsize(path)
        assert len(sent) < 200, "aborted mid-stream, not after buffering the body"

        # Content-Length is honoured before a single byte is read
        fake_fetch(lambda req: httpx.Response(200, content=b"", headers={
            "content-type": "video/mp4", "content-length": "99999999"}))
        with open(path, "wb") as f:
            assert (await gateway.fetch_video("https://big.test/a.mp4", f))[1] == "too-large"
        gateway.MAX_VIDEO_MB = 64.0
        os.unlink(path)
    asyncio.run(go())


def test_redirect_revalidated_every_hop():
    async def go():
        seen = fake_dns({"good.test": ["93.184.216.34"], "evil.test": ["169.254.169.254"]})

        def handler(req):
            if req.url.host == "good.test" and req.url.path == "/hop":
                return httpx.Response(302, headers={"location": "http://evil.test/latest/meta-data/"})
            return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})

        fake_fetch(handler)
        with open("/dev/null", "wb") as f:
            assert (await gateway.fetch_video("https://good.test/hop", f))[1] == "blocked-address"
        assert seen == ["good.test", "evil.test"], seen          # the hop was re-resolved

        # redirect budget
        fake_fetch(lambda req: httpx.Response(302, headers={"location": "https://good.test/loop"}))
        seen.clear()
        with open("/dev/null", "wb") as f:
            assert (await gateway.fetch_video("https://good.test/loop", f))[1] == "too-many-redirects"
        assert len(seen) == gateway.MAX_REDIRECTS + 1, seen
    asyncio.run(go())


def test_content_type_allowlist():
    async def go():
        fake_dns({"ok.test": ["93.184.216.34"]})
        fake_fetch(lambda req: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))
        with open("/dev/null", "wb") as f:
            assert (await gateway.fetch_video("https://ok.test/a.mp4", f))[1] == "unsupported-type"
        # server says nothing -> the extension decides
        fake_fetch(lambda req: httpx.Response(200, content=MP4, headers={"content-type": "application/octet-stream"}))
        with open("/dev/null", "wb") as f:
            assert (await gateway.fetch_video("https://ok.test/a.mov", f)) == ("video/quicktime", None)
            assert (await gateway.fetch_video("https://ok.test/a.exe", f))[1] == "unsupported-type"
    asyncio.run(go())


def test_vllm_gets_a_data_url_not_the_original():
    """End to end through the app: the body vLLM receives carries the bytes inline."""
    from fastapi.testclient import TestClient

    fake_dns({"cdn.test": ["93.184.216.34"]})
    fake_fetch(lambda req: httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"}))
    gateway.probe_seconds = lambda path: 10.1
    seen = {}

    def vllm(request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "a bus"}}],
                                         "usage": {"prompt_tokens": 2061, "completion_tokens": 7}})

    gateway.client = httpx.AsyncClient(base_url="http://vllm.local", transport=httpx.MockTransport(vllm))
    gateway.LEGACY_KEY = gateway.SUPABASE_URL = ""   # unauthenticated, whatever the other test module set
    with TestClient(gateway.app) as tc:
        r = tc.post("/v1/chat/completions", json={"model": "marlin2b", "messages": [
            {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "https://cdn.test/clip.mp4"}},
                                         {"type": "text", "text": "caption"}]}]})
    assert r.status_code == 200, r.text
    body = seen["body"]
    assert "cdn.test" not in body, "the caller's URL must not reach vLLM"
    assert f"data:video/mp4;base64,{base64.b64encode(MP4).decode()}" in body
    assert '"mm_processor_kwargs"' in body            # duration still drives the budget

    # a URL the fetcher rejects is a generic 400 with a reason class, no exception text
    with TestClient(gateway.app) as tc:
        r = tc.post("/v1/chat/completions", json={"model": "marlin2b", "messages": [
            {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "http://169.254.169.254/x.mp4"}}]}]})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["message"] == "could not fetch video (blocked-address)", r.text


def test_data_url_still_works():
    async def go():
        gateway.probe_seconds = lambda path: 3.0
        url = "data:video/mp4;base64," + base64.b64encode(MP4).decode()
        secs, out, err = await gateway.prepare_video({"video_url": {"url": url}})
        assert err is None and secs == 3.0 and out == url, (secs, err)

        gateway.MAX_VIDEO_MB = 0.00001
        assert (await gateway.prepare_video({"video_url": {"url": url}}))[2].startswith("video larger than")
        gateway.MAX_VIDEO_MB = 64.0
        assert (await gateway.prepare_video({"video_url": {"url": "ftp://x/y.mp4"}}))[2].startswith("video_url must be")
    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
