"""auth: sha256(key) -> api_keys row, cached.

Bounded, insertion-ordered: the hash is caller-supplied, so an unbounded dict
is a memory-exhaustion vector. Misses live in their own small cache so a flood
of random keys cannot evict the real ones.
"""
import asyncio
import hashlib
import hmac
import time
from collections import OrderedDict


def put(cache, k, v, cap):
    cache[k] = v
    cache.move_to_end(k)
    while len(cache) > cap:
        cache.popitem(last=False)


class Auth:
    """One per app: the three caches are instance state, not module state."""

    def __init__(self, rt):
        self.rt = rt
        self.keys = OrderedDict()       # key_hash -> (expires_at, row); keys Supabase knows
        self.misses = OrderedDict()     # key_hash -> (expires_at, None); keys it does not
        self.last_used = OrderedDict()  # key_hash -> ts of the last last_used_at PATCH

    async def authenticate(self, req):
        """Returns (api_keys row, error status). The row is None both for the legacy
        key and when nothing is configured; the status is None when the call is allowed."""
        s, now = self.rt.settings, self.rt.clock
        token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if s.legacy_key and hmac.compare_digest(token.encode(), s.legacy_key.encode()):
            return None, None
        if not s.supabase_url:
            return None, 401 if s.legacy_key else None
        if not token:
            return None, 401
        h = hashlib.sha256(token.encode()).hexdigest()
        hit = self.keys.get(h) or self.misses.get(h)
        if hit is None or hit[0] < now():
            try:
                r = await self.rt.sb.get("/api_keys", params={"key_hash": f"eq.{h}", "select": "id,org_id,revoked_at"})
                r.raise_for_status()
                rows = r.json()
                hit = (now() + (s.key_ttl if rows else s.miss_ttl), rows[0] if rows else None)
                (self.misses if rows else self.keys).pop(h, None)   # a key only lives in one of the two
                put(*((self.keys, h, hit, s.key_cache_max) if rows else (self.misses, h, hit, s.miss_cache_max)))
            except Exception as e:
                if hit is None:  # never seen this key and Supabase is down: fail closed, but retryable
                    print(f"gateway: api_keys lookup failed ({type(e).__name__}: {e})", flush=True)
                    return None, 503
        row = hit[1]
        if row is None or row.get("revoked_at"):
            return None, 401
        if now() - self.last_used.get(h, 0) > s.last_used_ttl:
            put(self.last_used, h, now(), s.key_cache_max)
            asyncio.create_task(self.touch(h))
        return row, None

    async def touch(self, h):
        """Fire-and-forget api_keys.last_used_at update (at most once a minute per key)."""
        try:
            await self.rt.sb.patch("/api_keys", params={"key_hash": f"eq.{h}"},
                                   json={"last_used_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                   headers={"Prefer": "return=minimal"})
        except Exception as e:
            print(f"gateway: last_used_at update failed ({type(e).__name__}: {e})", flush=True)
