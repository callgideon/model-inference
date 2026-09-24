#!/usr/bin/env python3
"""One gateway process of the UPLOAD-RESTART stack drill (`test_upload_restart_stack.py`).

    python -m tests.m.upload_stack_step METHOD PATH [--json BODY] [--data FILE]
        [--token consumer|other] [--authority durable|process] [--die-after STEP]

It composes `create_app` as the cutover does (every router; the contract fakes for D's job,
stream and catalog stores, which this drill does not test) over the deployment's SHARED
state - the task-local PostgreSQL (`M5_DSN`, D10's `PgLifecycle`) and MinIO (`M5_S3_*`) -
makes ONE mounted call, prints its answer as one JSON line and exits. Nothing survives the
process but what those two hold. `--authority process` is today's composition (M3's
in-process tickets): the negative control. `--die-after` ends the process with
`os._exit(17)` right after that durable step returns: `acknowledge_put`, `complete`,
`put_if_absent:uploads/` (the destination written) or `put_if_absent:media/` (the source).

The DSN and the MinIO literals come from the parent's environment and are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import sys

import httpx

DIED = 17


class DiesAfter:
    """`inner` whose `step` returns and then the process is gone (no answer, no cleanup)."""

    def __init__(self, inner, step: str, prefix: str = "") -> None:
        self.inner, self.step, self.prefix = inner, step, prefix

    def __getattr__(self, name):
        attribute = getattr(self.inner, name)
        if name != self.step:
            return attribute

        async def committed_then_died(*args, **kw):
            answer = await attribute(*args, **kw)
            if name != "put_if_absent" or str(args[0]).startswith(self.prefix):
                sys.stdout.flush()
                os._exit(DIED)
            return answer

        return committed_then_died


def compose(authority: str, die_after: str | None):
    from infrx.contracts.fakes.factories import credit_jobstore_factory
    from infrx.gateway import pilot
    from infrx.gateway.app import create_app
    from infrx.media.s3 import S3ObjectStore
    from infrx.media.uploads import MediaUploads
    from infrx.scheduling.memory import MemoryScheduler

    from ..g import support as gs
    from .test_pilot_media import Journal
    from .test_upload_wiring import keyed_identities

    objects = S3ObjectStore.connect(os.environ["M5_S3_BUCKET"], os.environ["M5_S3_PREFIX"],
                                    os.environ["M5_S3_ENDPOINT"])
    step, _, prefix = (die_after or "").partition(":")
    if step == "put_if_absent":
        objects = DiesAfter(objects, step, prefix)
    harness = credit_jobstore_factory()
    jobs = harness.port
    jobs.catalog = catalog = gs.catalog()
    config = gs.settings()
    config.deployment = config.deployment.replace(accounting_regime="credit")
    config.pilot = config.pilot.replace(active_rate_card_version=catalog.rate_cards[
        gs.IDS.prod_deployment].rate_card_version)
    if authority == "durable":
        from infrx.state.jobstore import connector
        from infrx.state.lifecycle import PgLifecycle                   # D10
        lifecycle = PgLifecycle(connector(os.environ["M5_DSN"]), limits=config.pilot)
        tickets = DiesAfter(lifecycle, step) if step in ("acknowledge_put", "complete") \
            else lifecycle
        # M5 wiring request 1, as the pilot will compose it.
        pilot.MediaUploads = functools.partial(MediaUploads, uploads=tickets, content=lifecycle)
    app = create_app(config, client=gs.upstream(), sb=keyed_identities(), catalog=catalog,
                     stream=Journal(jobs), objects=objects, jobs=jobs,
                     index=MemoryScheduler(harness.clock.now))
    return app, jobs


async def once(app, method, path, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway.test") as client:
        return await client.request(method, path, **kw)


def main(argv=None) -> int:
    from ..g import support as gs
    from .test_pilot_media import OTHER_TOKEN

    parser = argparse.ArgumentParser()
    parser.add_argument("method")
    parser.add_argument("path")
    parser.add_argument("--json")
    parser.add_argument("--data")
    parser.add_argument("--content-type", default="video/mp4")
    parser.add_argument("--token", choices=("consumer", "other"), default="consumer")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--authority", choices=("durable", "process"), default="durable")
    parser.add_argument("--die-after")
    args = parser.parse_args(argv)
    app, jobs = compose(args.authority, args.die_after)
    headers = {"authorization": "Bearer " + (gs.TOKEN if args.token == "consumer"
                                             else OTHER_TOKEN)}
    if args.idempotency_key:
        headers["idempotency-key"] = args.idempotency_key
    kw = {"headers": headers}
    if args.json is not None:
        kw["json"] = json.loads(args.json)
    if args.data is not None:
        with open(args.data, "rb") as handle:
            kw["content"] = handle.read()
        headers["content-type"] = args.content_type
    response = asyncio.run(once(app, args.method, args.path, **kw))
    try:
        body = response.json()
    except ValueError:
        body = response.text
    admitted = [{"handle": ref.handle, "kind": ref.kind.value, "digest": ref.digest,
                 "bytes": ref.bytes, "storage_ref": ref.storage_ref}
                for job in jobs.jobs.values() for ref in job.request.media]
    print(json.dumps({"status": response.status_code, "body": body, "admitted": admitted,
                      "replayed": response.headers.get("idempotency-replayed")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
