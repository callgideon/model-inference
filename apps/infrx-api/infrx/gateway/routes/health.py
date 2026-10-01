"""The gateway's `/health`: up (200) or down (503) by the engine's own `/health`.

deploy/Caddyfile proxies the public `/health` here and answers its own generic body
(`{"ok":true}` / `{"ok":false}`), so nothing this body carries reaches a client; the
loopback readers (deploy/lib.sh, rehearse.sh) read the status only.
"""
from fastapi.responses import JSONResponse


def register(app, rt):
    @app.get("/health")
    async def health():
        try:
            r = await rt.client.get("/health", timeout=5)
            return JSONResponse({"ok": r.status_code == 200},
                                status_code=200 if r.status_code == 200 else 503)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    return health
