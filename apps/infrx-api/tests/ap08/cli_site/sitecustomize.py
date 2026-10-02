"""AP-08's CLI proof (`test_judge_cli_pg.py`): loaded by the real `python -m infrx.lab.workers
judge` only when that test puts this directory on PYTHONPATH and names its world in AP08_WORLD.
It replaces exactly what ap8 does not run - the trace store (ap7's ClickHouse + S3: T3's
retention over in-memory traces, the eligible read AP-07 publishes) - and prices the fake judge
at J2's TEST rates (never an approved price; P-10 stays BLOCKED). Nothing else is touched: the
role's composition, its passes, the ledger, the doors and the judge fake's HTTP are the real ones.
"""
import asyncio
import json
import os

if os.environ.get("AP08_WORLD"):
    import clickhouse_connect

    from infrx.judge import cost, start
    from infrx.media import s3
    from infrx.media.store import InMemoryObjectStore
    from infrx.traces import retention
    from tests.j import fakes as j1
    from tests.j.submit import fakes

    with open(os.environ["AP08_WORLD"]) as world_file:
        WORLD = json.load(world_file)
    projection, objects = fakes.Projection(), InMemoryObjectStore()
    for trace in WORLD["traces"]:
        asyncio.run(fakes.trace(projection, objects, WORLD["grantor"], trace["request_id"]))
    stored = fakes.retention(projection, objects)
    retention.Retention = lambda *args, **kwargs: stored
    clickhouse_connect.get_client = lambda **kwargs: object()
    s3.S3ObjectStore.connect = classmethod(lambda cls, *args, **kwargs: object())
    cost.APPROVED_RATES = j1.TEST_RATES

    async def eligible(grantor: str, model: str, limit: int):
        if (grantor, model) != (WORLD["grantor"], WORLD["model"]):
            return []
        return [(t["request_id"], t["video"]) for t in WORLD["traces"]][:limit]

    start.eligible_read = lambda *_: eligible
