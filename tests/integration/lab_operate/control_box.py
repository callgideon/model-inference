"""E3L's Lab control-service process (l10, l12): R186's factory
`infrx.lab.control.app:create_app` under uvicorn on the e3l block's control port, composed from
its own `INFRX_LAB_*` settings only.

    python control_box.py <port>

`E3L_HOLD=<PgControlStore method>` (l10's fault point): that store call runs and commits, then
`E3L_HOLD_MARKER` is written and the request never answers - the process is SIGKILLed there,
between committing an operation and answering it.

`E3L_ENGINE_URL=<the controlled engine>` (R203): the factory's dev smoke is `EngineSmoke`'s
rule - the engine answers its model list - in place of `NoEngine`'s 503, because WR-L3-2's
engine smoke adapter is not wired.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


def install_hold(method: str, marker: Path) -> None:
    from infrx.state.lab_control import PgControlStore
    committed = getattr(PgControlStore, method)

    async def held(self, *args, **kwargs):
        result = await committed(self, *args, **kwargs)
        marker.write_text(json.dumps({"method": method, "pid": os.getpid()}))
        await asyncio.sleep(3600)
        return result
    setattr(PgControlStore, method, held)


def install_engine(url: str) -> None:
    import httpx

    from infrx.lab.control.app import NoEngine

    async def smoke(self, serving, deployment) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(f"{url.rstrip('/')}/v1/models")).status_code == 200
    NoEngine.smoke = smoke


def main(argv: list[str]) -> int:
    if os.environ.get("E3L_ENGINE_URL"):
        install_engine(os.environ["E3L_ENGINE_URL"])
    if os.environ.get("E3L_HOLD"):
        install_hold(os.environ["E3L_HOLD"], Path(os.environ["E3L_HOLD_MARKER"]))
    import uvicorn

    from infrx.lab.control.app import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=int(argv[1]), log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
