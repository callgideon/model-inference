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
        assert len(GETS) == 1 and gateway._keys[hashlib.sha256(b"nope").hexdigest()][1] is None
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
