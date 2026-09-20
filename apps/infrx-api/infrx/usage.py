"""usage: bounded background queue -> Supabase usage_events, with disk spill.

Legacy shipper, unchanged by F1: rows that never land go to usage_failed.jsonl
for deploy/replay_usage.py.
"""
import asyncio
import json


def cost(prompt_tokens, completion_tokens, prices):
    """0 when prices are unknown or unusable: never drop a usage row over a price."""
    try:
        return round((prompt_tokens or 0) * float(prices["input_usd_per_m"]) / 1e6
                     + (completion_tokens or 0) * float(prices["output_usd_per_m"]) / 1e6, 8)
    except Exception:
        return 0.0


class Usage:
    """One per app: queue, worker task and price cache are instance state."""

    def __init__(self, rt):
        self.rt = rt
        self.q = asyncio.Queue(maxsize=10000)
        self.worker = None
        self.prices = (0.0, None)  # (expires_at, {input_usd_per_m, output_usd_per_m} or None)

    async def get_prices(self):
        s, now = self.rt.settings, self.rt.clock
        if self.prices[0] < now():
            try:
                r = await self.rt.sb.get("/models", params={"id": f"eq.{s.model_id}",
                                                            "select": "input_usd_per_m,output_usd_per_m"})
                r.raise_for_status()
                rows = r.json()
                if not rows:
                    print(f"gateway: no prices for {s.model_id}; cost_usd=0", flush=True)
                self.prices = (now() + s.price_ttl, rows[0] if rows else None)
            except Exception as e:
                self.prices = (now() + s.price_ttl, self.prices[1])  # keep the last known prices
                if self.prices[1] is None:
                    print(f"gateway: price fetch failed ({type(e).__name__}: {e}); cost_usd=0", flush=True)
        return self.prices[1]

    def spill(self, row):
        failed_log = self.rt.settings.usage_failed_log
        try:
            with open(failed_log, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            print(f"gateway: cannot write {failed_log} ({e}); lost {row.get('id')}", flush=True)

    async def ingest(self):
        while True:
            row = await self.q.get()
            row["cost_usd"] = cost(row["prompt_tokens"], row["completion_tokens"], await self.get_prices())
            for delay in self.rt.settings.retry_delays:  # three retries, then spill to disk
                try:
                    r = await self.rt.sb.post("/usage_events", json=row, headers={"Prefer": "return=minimal"})
                    if r.status_code < 300 or r.status_code == 409:  # 409 = already inserted
                        break
                    raise RuntimeError(f"{r.status_code} {r.text[:200]}")
                except Exception as e:
                    if not delay:
                        print(f"gateway: usage_events insert failed ({e}) -> "
                              f"{self.rt.settings.usage_failed_log}", flush=True)
                        self.spill(row)
                        break
                    await asyncio.sleep(delay)

    def enqueue(self, row):
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self.ingest())
        try:
            self.q.put_nowait(row)
        except asyncio.QueueFull:
            print("gateway: usage queue full", flush=True)
            self.spill(row)
