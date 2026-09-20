import json

from fastapi import Request


def register(app, rt):
    @app.get("/v1/models")
    async def models(req: Request):
        with open(rt.settings.models_doc) as f:
            doc = json.load(f)
        # OpenAI-shaped list for ordinary clients; OpenRouter's provider document fields ride along.
        for m in doc["data"]:
            m.setdefault("object", "model")
            m.setdefault("owned_by", "nemostation")
        return doc

    return models
