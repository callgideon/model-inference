from fastapi.responses import JSONResponse


def register(app, rt):
    @app.get("/health")
    async def health():
        try:
            r = await rt.client.get("/health", timeout=5)
            return JSONResponse({"ok": r.status_code == 200, "inflight": rt.inflight},
                                status_code=200 if r.status_code == 200 else 503)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    return health
