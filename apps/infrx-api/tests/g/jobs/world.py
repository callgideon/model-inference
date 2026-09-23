"""G3's in-process world: G2's (`relay_support.World`) with the jobs router mounted, plus an
ASGI driver for any method and path that can disconnect on cue.

`restart()` mounts the jobs router the way the cutover will: `rt.relay` and `rt.ingress` on the
runtime, `jobs.register(app, rt)` after the ingress. The store clock is the contract fake's
(`FakeJobStore.clock`); `db_now` - `PgJobStore`'s store-clock read, which the fake lacks - is
added here over that clock. The gateway clock (`relay.clock`, the app's) is separate, so a
case can move one without the other.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

from infrx.gateway.routes import jobs as jobs_router

from .. import relay_support as rs, support

# A consumer key of another organization, and the fixed request id a byte-for-byte comparison
# of two refusals needs.
OTHER_ORG = "5e5e5e5e-0000-4000-8000-000000000005"
OTHER_ROW = {**support.ROW, "id": "6f6f6f6f-0000-4000-8000-000000000006", "org_id": OTHER_ORG,
             "user_id": "7a7a7a7a-0000-4000-8000-000000000007"}
FIXED_ID = "8b8b8b8b-0000-4000-8000-000000000008"


class JobsWorld(rs.World):
    new_request_id: Callable | None = None

    def restart(self) -> None:
        super().restart()
        rt = self.app.state.runtime
        rt.relay = self.relay
        rt.ingress = support.deps(accept=self.relay.accept, catalog=self.catalog,
                                  **({"new_request_id": self.new_request_id}
                                     if self.new_request_id else {}))
        self.router = jobs_router.register(self.app, rt)
        self.jobs.db_now = self.db_now

    async def db_now(self):
        return self.clock.now()

    def as_key(self, row) -> None:
        """The next process authenticates every request as `row`'s key (same stores)."""
        self.row = row
        self.restart()

    def handle(self, job=None) -> str:
        job = job or self.only_job()
        return job.credit.job_handle if job.credit is not None else job.admission.job_handle


async def send(app, method: str, path: str, *, body: dict | None = None, key: str | None = None,
               headers: dict | None = None, leave: asyncio.Event | None = None,
               on_send: Callable | None = None) -> rs.Reply:
    """One request over ASGI. Once `leave` is set the next `receive` is `http.disconnect`;
    `on_send(message)` sees every message as it is sent."""
    raw = json.dumps(body).encode() if body is not None else b""
    sent: list = []
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        await (leave or asyncio.Event()).wait()
        return {"type": "http.disconnect"}

    async def deliver(message):
        sent.append(message)
        if on_send is not None:
            on_send(message)

    head = {**(support.RAW if body is not None else support.AUTH),
            **({"idempotency-key": key} if key else {}), **(headers or {})}
    path_only, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": method, "path": path_only,
             "raw_path": path_only.encode(), "query_string": query.encode(), "root_path": "",
             "scheme": "http", "client": ("198.51.100.7", 40000), "server": ("gw", 8001),
             "headers": [(k.lower().encode(), v.encode()) for k, v in head.items()]}
    await app(scope, receive, deliver)
    return rs.Reply(sent)


def job_path(handle: str, tail: str = "") -> str:
    return f"{jobs_router.JOBS_PATH}/{handle}{tail}"
