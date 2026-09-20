"""POST /v1/chat/completions: auth, capacity, video budget, `<think>` stripping,
usage. Lifted from gateway.py unchanged; the known weaknesses (429 checked after
auth but before media, legacy usage row shape, the streaming strip heuristic)
stay as they are until track G changes them deliberately.
"""
import json
import re
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

THINK = re.compile(r"^\s*<think>(?:.*?</think>)?\s*", re.S)


def register(app, rt):
    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        s = rt.settings
        key, err = await rt.auth.authenticate(req)
        if err == 401:
            return JSONResponse({"error": {"message": "invalid api key", "type": "authentication_error"}}, status_code=401)
        if err:
            return JSONResponse({"error": {"message": "cannot verify api key, retry", "type": "server_error"}},
                                status_code=err, headers={"Retry-After": "5"})
        if rt.inflight >= s.max_inflight:
            return JSONResponse({"error": {"message": "at capacity, retry", "type": "rate_limit_error"}}, status_code=429,
                                headers={"Retry-After": "2"})
        body = await req.json()
        rid = str(uuid.uuid4())
        t0 = rt.clock()
        body["model"] = "marlin2b"  # vLLM's served name
        secs = 0.0
        n_video = 0
        for msg in body.get("messages", []):
            if isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if part.get("type") in ("video_url", "input_video"):
                        n_video += 1
                        if n_video > 1:
                            return JSONResponse({"error": {"message": "one video per request", "type": "invalid_request_error"}}, status_code=400)
                        secs, data_url, err = await rt.media.prepare_video(part)
                        if err:
                            return JSONResponse({"error": {"message": err, "type": "invalid_request_error"}}, status_code=400)
                        if isinstance(part.get("video_url"), dict):   # hand vLLM the bytes, not the URL
                            part["video_url"]["url"] = data_url
                        else:
                            part["video_url"] = data_url
        if n_video:
            body["mm_processor_kwargs"] = rt.media.budget_kwargs(secs)
        stream = bool(body.get("stream"))
        if stream:
            body.setdefault("stream_options", {})["include_usage"] = True

        rt.inflight += 1
        usage = {"id": rid, "ts": t0, "video_seconds": secs, "stream": stream}

        def log(status, first=None, u=None):
            usage.update({"status": status, "ttft_s": round((first or rt.clock()) - t0, 3),
                          "wall_s": round(rt.clock() - t0, 3),
                          "prompt_tokens": (u or {}).get("prompt_tokens"), "completion_tokens": (u or {}).get("completion_tokens")})
            with open(s.usage_log, "a") as f:
                f.write(json.dumps(usage) + "\n")
            if key:  # legacy-key requests have no org, so they stay in usage.jsonl only
                rt.usage.enqueue({"id": rid, "org_id": key["org_id"], "api_key_id": key["id"], "model_id": s.model_id,
                                  "status": status, "stream": stream, "prompt_tokens": usage["prompt_tokens"],
                                  "completion_tokens": usage["completion_tokens"], "video_seconds": round(secs, 3),
                                  "ttft_ms": int(usage["ttft_s"] * 1000), "latency_ms": int(usage["wall_s"] * 1000),
                                  "cached": False, "cost_usd": 0})

        headers = {"Inference-Id": rid}
        # Exactly one `rt.inflight -= 1` per path, in a finally: a decrement that runs
        # early (or twice) drifts the counter negative and MAX_INFLIGHT stops firing.
        if not stream:
            try:
                r = await rt.client.post("/v1/chat/completions", json=body)
                data = r.json()
                if r.status_code == 200:
                    for ch in data.get("choices", []):
                        if ch.get("message", {}).get("content"):
                            ch["message"]["content"] = THINK.sub("", ch["message"]["content"], count=1)
                    data["model"] = s.model_id
                log(r.status_code, None, data.get("usage"))
                return JSONResponse(data, status_code=r.status_code, headers=headers)
            except Exception as e:
                log(502)
                return JSONResponse({"error": {"message": f"upstream error: {e}", "type": "server_error"}},
                                    status_code=502, headers=headers)
            finally:
                rt.inflight -= 1

        async def gen():
            first = None
            u = None
            status = 200
            stripped = False
            try:
                async with rt.client.stream("POST", "/v1/chat/completions", json=body) as r:
                    status = r.status_code
                    if status != 200:
                        yield (await r.aread())
                        return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            if line == "":
                                continue
                            yield line + "\n\n"
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            yield "data: [DONE]\n\n"
                            continue
                        try:
                            obj = json.loads(payload)
                        except Exception:
                            yield line + "\n\n"
                            continue
                        obj["model"] = s.model_id
                        if obj.get("usage"):
                            u = obj["usage"]
                        for ch in obj.get("choices", []):
                            c = ch.get("delta", {}).get("content")
                            if c:
                                if first is None:
                                    first = rt.clock()
                                if not stripped:
                                    c2 = THINK.sub("", c, count=1)
                                    stripped = not c.lstrip().startswith("<think>") or c2 != c
                                    ch["delta"]["content"] = c2
                        yield "data: " + json.dumps(obj) + "\n\n"
            finally:
                rt.inflight -= 1
                log(status, first, u)

        return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

    return chat
