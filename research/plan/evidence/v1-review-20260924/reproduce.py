#!/usr/bin/env python3
"""Audit seam probes, not release acceptance tests. No network, Docker or real data.

Run from the repository root with apps/infrx-api/.venv/bin/python.
These assertions document defects at d7dc3690; a repair should change these outcomes.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "apps/infrx-api"), str(ROOT / "models/marlin2b")]
import httpx
import bench
from tests.g.uploads.test_uploads import mounted, bearer, ORG_A, CLIP
from infrx.media.uploads import MediaUploads
from infrx.media.store import InMemoryObjectStore
from infrx.media.gc import MediaCollector
from infrx.contracts.fakes.support import FakeClock
from infrx.contracts import errors


async def main():
    observations = []
    app, rt, _, _ = mounted()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://gateway.test") as client:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "video.mp4"
                path.write_bytes(CLIP)
                row, responses = {}, []

                async def observe(response):
                    await response.aread()
                    responses.append({"status": response.status_code,
                                      "code": response.json().get("error", {}).get("code")})

                client.event_hooks = {"response": [observe]}
                cfg = {"base": "http://gateway.test/v1", "headers": bearer(),
                       "headers_for": lambda tenant: bearer(), "key": "synthetic"}
                try:
                    await bench.upload(client, cfg, str(path), row)
                except bench.UploadFailed:
                    pass
                assert row["upload_status"] == 400
                observations.append({"probe": "benchmark_against_real_upload_route",
                                     "responses": responses})
    finally:
        await rt.client.aclose()
        await rt.sb.aclose()

    objects = InMemoryObjectStore()
    before = MediaUploads(objects)
    ticket = await before.create_upload(ORG_A, {"bytes": len(CLIP)})
    handle = ticket["upload_handle"]
    await before.put_upload(ORG_A, handle, CLIP, "video/mp4")
    ref = await before.finalize_upload(ORG_A, handle)
    after = MediaUploads(objects)
    try:
        await after.resolve_owned(ORG_A, handle)
    except errors.NotFound:
        assert await objects.get(ref.storage_ref) is not None
        observations.append({"probe": "upload_after_gateway_reconstruction",
                             "outcome": "not_found", "source_bytes_present": True})
    else:
        raise AssertionError("Upload restart behavior changed; re-audit it")

    # A restart loses the local attachment index. Even with a supplied durable attachment
    # reader, the collector enumerates no durable jobs: it only inspects local dictionaries.
    clock, live_queries, attachment_queries = FakeClock(), [], []

    class DurableAttachments:
        async def get(self, job_id):
            attachment_queries.append(job_id)
            return (ref,)

    async def is_live(job_id):
        live_queries.append(job_id)
        return True

    restarted = MediaUploads(objects, now=clock.now, attachments=DurableAttachments())
    collector = MediaCollector(restarted, is_live=is_live, grace_s=1)
    await collector.sweep()
    clock.advance(2)
    swept = await collector.sweep()
    assert ref.storage_ref in swept.deleted
    assert not live_queries and not attachment_queries
    observations.append({"probe": "collector_after_gateway_reconstruction",
                         "source_deleted_after_grace": True,
                         "live_job_queries": len(live_queries),
                         "durable_attachment_queries": len(attachment_queries),
                         "limit": "Simulated restart with shared object store; no PostgreSQL used"})
    print(json.dumps({"reviewed_base": "d7dc3690", "observations": observations}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
