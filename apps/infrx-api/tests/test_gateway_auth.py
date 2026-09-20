#!/usr/bin/env python3
"""Gateway auth cache and cost maths, against a fake Supabase.

    python3 -m pytest apps/infrx-api/tests/test_gateway_auth.py
    python3 apps/infrx-api/tests/test_gateway_auth.py     # same checks, no pytest
"""
import asyncio, hashlib, json, os, sys, time

os.environ.update(SUPABASE_URL="https://fake.supabase.co", SUPABASE_SERVICE_ROLE_KEY="service-role",
                  GATEWAY_API_KEY="legacy-key", USAGE_LOG="/tmp/gw-test-usage.jsonl")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import gateway

ROW = {"id": "11111111-1111-1111-1111-111111111111", "org_id": "22222222-2222-2222-2222-222222222222",
       "revoked_at": None}
GETS = []


class Req:
    def __init__(self, token):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def fake_supabase(rows=(ROW,), down=False):
    """Point gateway.sb at a MockTransport; records the GETs it serves."""
    def handler(request):
        if down:
            raise httpx.ConnectError("supabase unreachable")
        if request.method == "GET":
            GETS.append(str(request.url))
        return httpx.Response(200, json=list(rows))
    gateway.sb = httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                                   transport=httpx.MockTransport(handler))


def reset():
    GETS.clear()
    gateway._keys.clear()
    gateway._misses.clear()
    gateway._last_used.clear()


def test_hash_lookup_and_cache():
    async def go():
        reset()
        fake_supabase()
        row, err = await gateway.authenticate(Req("sk-infrx-abc"))
        assert err is None and row["org_id"] == ROW["org_id"], (row, err)
        want = hashlib.sha256(b"sk-infrx-abc").hexdigest()
        assert f"key_hash=eq.{want}" in GETS[0], GETS[0]

        row, err = await gateway.authenticate(Req("sk-infrx-abc"))  # cached: no second GET
        assert err is None and len(GETS) == 1, GETS

        exp, cached = gateway._keys[want]                            # expire it
        gateway._keys[want] = (time.time() - 1, cached)
        await gateway.authenticate(Req("sk-infrx-abc"))
        assert len(GETS) == 2, GETS
        await asyncio.sleep(0)
    asyncio.run(go())


def test_revoked_and_unknown():
    async def go():
        reset()
        fake_supabase(rows=[dict(ROW, revoked_at="2026-09-20T00:00:00Z")])
        assert await gateway.authenticate(Req("revoked")) == (None, 401)
        reset()
        fake_supabase(rows=[])
        assert await gateway.authenticate(Req("nope")) == (None, 401)
        assert len(GETS) == 1 and gateway._misses[hashlib.sha256(b"nope").hexdigest()][1] is None
    asyncio.run(go())


def test_supabase_down():
    async def go():
        reset()
        fake_supabase()
        row, err = await gateway.authenticate(Req("sk-infrx-abc"))   # warm the cache
        assert err is None, err
        fake_supabase(down=True)
        gateway._keys[hashlib.sha256(b"sk-infrx-abc").hexdigest()] = (0, row)  # stale
        assert (await gateway.authenticate(Req("sk-infrx-abc")))[1] is None    # served from cache
        assert await gateway.authenticate(Req("never-seen")) == (None, 503)    # 503, not 401
        await asyncio.sleep(0)
    asyncio.run(go())


def test_legacy_key():
    async def go():
        reset()
        fake_supabase()
        assert await gateway.authenticate(Req("legacy-key")) == (None, None)   # allowed, no org
        assert GETS == []                                                      # no Supabase call
        assert await gateway.authenticate(Req("")) == (None, 401)
    asyncio.run(go())


def test_cost():
    prices = {"input_usd_per_m": "0.10", "output_usd_per_m": "0.40"}
    assert gateway.cost(1_000_000, 0, prices) == 0.1
    assert gateway.cost(2_061, 512, prices) == round(2061 * 0.1 / 1e6 + 512 * 0.4 / 1e6, 8)
    assert gateway.cost(None, None, prices) == 0.0
    assert gateway.cost(2_061, 512, None) == 0.0          # prices unavailable -> free


def test_usage_row_spills_when_supabase_is_down():
    async def go():
        gateway.USAGE_FAILED_LOG = "/tmp/gw-test-usage-failed.jsonl"
        gateway.RETRY_DELAYS = (0, 0)
        open(gateway.USAGE_FAILED_LOG, "w").close()
        fake_supabase(down=True)
        gateway._prices = (time.time() + 300, {"input_usd_per_m": "1", "output_usd_per_m": "2"})
        row = {"id": "33333333-3333-3333-3333-333333333333", "org_id": ROW["org_id"],
               "api_key_id": ROW["id"], "model_id": gateway.MODEL_ID, "status": 200, "stream": False,
               "prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "video_seconds": 10.0,
               "ttft_ms": 500, "latency_ms": 900, "cached": False, "cost_usd": 0}
        gateway._usage_q.put_nowait(row)
        task = asyncio.create_task(gateway.ingest())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if os.path.getsize(gateway.USAGE_FAILED_LOG):
                break
        task.cancel()
        spilled = json.loads(open(gateway.USAGE_FAILED_LOG).read().strip())
        assert spilled["id"] == row["id"] and spilled["cost_usd"] == 3.0, spilled
    asyncio.run(go())


def test_legacy_key_is_compared_in_constant_time():
    """The legacy key must go through hmac.compare_digest, not ==."""
    import hmac as real_hmac

    calls = []

    class Recorder:
        @staticmethod
        def compare_digest(a, b):
            calls.append((a, b))
            return real_hmac.compare_digest(a, b)

    async def go():
        reset()
        fake_supabase()
        gateway.hmac = Recorder
        try:
            assert await gateway.authenticate(Req("legacy-key")) == (None, None)
            assert calls, "authenticate did not use hmac.compare_digest"
            assert all(isinstance(x, bytes) for c in calls for x in c), calls
            sb_url, gateway.SUPABASE_URL = gateway.SUPABASE_URL, ""   # legacy key only
            try:                                                      # prefix, suffix, near-miss
                for wrong in ("legacy", "legacy-keyy", "Legacy-key", ""):
                    assert await gateway.authenticate(Req(wrong)) == (None, 401), wrong
            finally:
                gateway.SUPABASE_URL = sb_url
            assert GETS == [], "a wrong legacy key must not be accepted"
        finally:
            gateway.hmac = real_hmac
    asyncio.run(go())


def test_miss_cache_is_bounded_and_spares_real_keys():
    """A flood of unknown keys is capped and never evicts a key Supabase knows."""
    async def go():
        reset()
        assert gateway.KEY_CACHE_MAX == 10_000
        fake_supabase()
        await gateway.authenticate(Req("sk-infrx-real"))          # one positive, cached
        real = hashlib.sha256(b"sk-infrx-real").hexdigest()
        assert real in gateway._keys

        fake_supabase(rows=[])                                     # everything else is unknown
        gateway.MISS_CACHE_MAX = 50
        try:
            for i in range(500):
                assert await gateway.authenticate(Req(f"junk-{i}")) == (None, 401)
            assert len(gateway._misses) == 50, len(gateway._misses)
            assert list(gateway._keys) == [real], list(gateway._keys)   # untouched by the flood
        finally:
            gateway.MISS_CACHE_MAX = 1_000
        await asyncio.sleep(0)
    asyncio.run(go())


def test_key_and_last_used_caches_are_bounded():
    """_keys and _last_used are LRU-capped too, oldest evicted first."""
    async def go():
        reset()
        fake_supabase()
        gateway.KEY_CACHE_MAX = 10
        try:
            for i in range(30):
                row, err = await gateway.authenticate(Req(f"sk-{i}"))
                assert err is None, err
            assert len(gateway._keys) == 10, len(gateway._keys)
            assert len(gateway._last_used) <= 10, len(gateway._last_used)
            assert hashlib.sha256(b"sk-29").hexdigest() in gateway._keys      # newest kept
            assert hashlib.sha256(b"sk-0").hexdigest() not in gateway._keys   # oldest gone
        finally:
            gateway.KEY_CACHE_MAX = 10_000
        await asyncio.sleep(0)
    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
