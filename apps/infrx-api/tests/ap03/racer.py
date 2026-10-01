"""One API instance in its own process for `test_actions_pg.race`: the console routes over
the PostgreSQL repository, `n` concurrent calls, the answers as JSON on stdout.

    python -m tests.ap03.racer <dsn> key|grant <user_id> <org_id> <n>
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from infrx.console.actions import ConsoleActions
from infrx.contracts import api
from infrx.gateway import control
from infrx.gateway.routes import console_actions
from infrx.state.jobstore import connector


async def main(dsn: str, kind: str, user: str, org: str, n: int) -> list[dict]:
    app = FastAPI()
    actor = api.Actor(audience="session", user_id=user, org_id=org)
    console_actions.register(app, SimpleNamespace(
        actors=control.StaticActors(actor), console_actions=ConsoleActions(connector(dsn)),
        feedback=None))
    path, body, headers = (("/console/v1/keys", {"name": "raced"}, {"Idempotency-Key": "race-1"})
                           if kind == "key" else ("/console/v1/signup-grant/claim", None, {}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://api.test", timeout=120) as client:
        answers = await asyncio.gather(*(client.post(path, json=body, headers=headers)
                                         for _ in range(n)))
    return [{"status": r.status_code, "body": r.json()} for r in answers]


if __name__ == "__main__":
    dsn, kind, user, org, n = sys.argv[1:6]
    print(json.dumps(asyncio.run(main(dsn, kind, user, org, int(n)))))
