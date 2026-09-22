"""G6B.c: the headless quickstart and the per-item dataset recipe, against an
in-process gateway (httpx.MockTransport). No server, no network, no real key.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib

import httpx
import pytest

from infrx.gateway.routes import validate
from infrx.operations import service

API_DIR = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("client_example", API_DIR / "client_example.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)

BASE = "https://gateway.test/v1"


@pytest.fixture
def key(monkeypatch):
    secret = service.new_secret()                  # minted per test; no demo credential
    monkeypatch.setenv("INFRX_API_KEY", secret)
    monkeypatch.delenv("MARLIN_API_KEY", raising=False)
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr(client, "SLEEP", no_sleep)
    return secret, slept


def completion(n=1):
    return httpx.Response(200, headers={"inference-id": "f0000001-0000-4000-8000-00000000000" + str(n)},
                          json={"choices": [{"message": {"content": "a bus passes"}}],
                                "usage": {"prompt_tokens": 1200, "completion_tokens": 40}})


def manifest(tmp_path, n=3):
    path = tmp_path / "items.jsonl"
    path.write_text("".join(json.dumps({
        "dataset_version": "sop-synth-v1", "source_id": "cam1", "episode_id": "ep1",
        "segment_index": i, "start_s": i * 120, "end_s": (i + 1) * 120,
        "prompt": "Caption.", "prompt_version": "p1", "profile_version": "marlin2b.video.v1",
        "video": f"https://media.test/clip{i}.mp4"}) + "\n" for i in range(n)))
    return str(path)


def sweep(tmp_path, handler, *extra, n=3):
    state = str(tmp_path / "state.jsonl")
    code = client.main(["sweep", "--base", BASE, "--manifest", manifest(tmp_path, n), "--state",
                        state, "--form", "url", *extra], transport=httpx.MockTransport(handler))
    rows = [json.loads(line) for line in open(state)]
    return code, rows, state


def test_api_ops__the_quickstart_runs_on_the_sync_path(key, capsys):
    secret, _ = key
    seen = []

    def handler(request):
        seen.append(request)
        return completion()
    code = client.main(["quickstart", "--base", BASE, "--video", "https://media.test/c.mp4",
                        "--find", "a white bus"], transport=httpx.MockTransport(handler))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "done" and out["completion_tokens"] == 40
    (req,) = seen
    body = json.loads(req.content)
    assert req.url == BASE + "/chat/completions" and req.headers["authorization"] == f"Bearer {secret}"
    assert "prefer" not in req.headers                      # ordinary chat never asks for 202
    # The pilot ingress refuses these by name (marlin-sop §2.2); the client never sends them.
    assert not {"mm_processor_kwargs", "stream_options", "stream"} & body.keys()
    assert body.keys() <= validate.SUPPORTED                # G1's own allow-list
    validate.check_messages(body)
    assert validate.execution_mode(body, req.headers).value == "sync"
    assert body["model"] == client.MODEL and '"a white bus"' in body["messages"][0]["content"][1]["text"]


def test_api_ops__a_resumed_sweep_resends_the_same_key_and_payload_and_skips_done_items(key, tmp_path):
    sent = []

    def flaky(request):
        sent.append((request.headers["idempotency-key"], request.content))
        n = len(sent)
        return completion() if n != 2 else httpx.Response(503, headers={"retry-after": "1"})
    code, rows, _ = sweep(tmp_path, flaky, "--concurrency", "1", "--max-attempts", "1")
    assert code == 1 and [r["status"] for r in rows] == ["done", "retry_later", "done"]
    first = list(sent)
    resent = []

    def healthy(request):
        resent.append((request.headers["idempotency-key"], request.content))
        return httpx.Response(200, headers={"idempotency-replayed": "true"},
                              json=completion().json())
    code, rows, _ = sweep(tmp_path, healthy, "--concurrency", "1")
    assert code == 0 and resent == [first[1]]               # same key, byte-identical body
    assert rows[-1]["status"] == "done" and rows[-1]["replayed"] is True
    assert all(k.startswith("sop1.") and len(k) == 5 + 32 for k, _ in first)
    assert len({k for k, _ in first}) == 3


def test_api_ops__failures_are_explicit_and_never_retried_blindly(key, tmp_path):
    secret, slept = key
    replies = iter([
        httpx.Response(400, json={"error": {"code": "unsupported_media"}}),
        httpx.Response(409, json={"error": {"code": "idempotency_conflict"}}),
        httpx.Response(410, json={"error": {"code": "idempotency_expired"}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "cut"}}]}),  # no usage
        httpx.Response(429, headers={"retry-after": "7"}),
        completion(),
    ])
    code, rows, state = sweep(tmp_path, lambda r: next(replies), "--concurrency", "1", n=5)
    assert [(r["status"], r["error_code"]) for r in rows] == [
        ("quarantined", "unsupported_media"), ("quarantined", "idempotency_conflict"),
        ("rerun_required", "idempotency_expired"), ("failed", None), ("done", None)]
    assert slept == [7.0] and rows[-1]["attempts"] == 2      # Retry-After honoured
    assert secret[9:] not in open(state).read()


@pytest.mark.parametrize("status,expected", [(401, "stopped_credential"),
                                             (402, "paused_wallet"),
                                             (403, "stopped_credential")])
def test_api_ops__an_exhausted_wallet_pauses_the_sweep(key, tmp_path, status, expected):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(status, json={"error": {"code": "insufficient_credit"}})
    code, rows, _ = sweep(tmp_path, handler, "--concurrency", "1")
    assert code == 1 and len(sent) == 1
    assert [r["status"] for r in rows] == [expected]         # nothing burned after it


def test_api_ops__concurrency_is_bounded_per_key(key, tmp_path):
    active, peak = [0], [0]

    async def handler(request):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        return completion()
    state = str(tmp_path / "s.jsonl")
    code = client.main(["sweep", "--base", BASE, "--manifest", manifest(tmp_path, 12), "--state",
                        state, "--form", "url", "--concurrency", "3"],
                       transport=httpx.MockTransport(handler))
    assert code == 0 and peak[0] == 3
    with pytest.raises(SystemExit):
        client.main(["sweep", "--base", BASE, "--manifest", manifest(tmp_path), "--state", state,
                     "--concurrency", "9"])


def test_api_ops__an_async_item_is_persisted_then_polled_to_its_result(key, tmp_path):
    handle = "job_" + "h" * 43

    def handler(request):
        if request.method == "POST":
            assert request.headers["prefer"] == "respond-async"
            return httpx.Response(202, json={"job_handle": handle})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"response": completion().json()})
        return httpx.Response(200, json={"cause": "completed", "result_available": True})
    code, rows, _ = sweep(tmp_path, handler, "--concurrency", "1", "--respond-async")
    assert code == 0
    first = [r for r in rows if r["item_key"] == rows[0]["item_key"]]
    assert [r["status"] for r in first] == ["accepted", "done"]   # handle written before polling
    assert first[0]["job_handle"] == handle


def test_api_auth__the_client_key_never_reaches_argv_or_state(key, tmp_path, capsys):
    secret, _ = key
    with pytest.raises(SystemExit):
        client.main(["quickstart", "--base", BASE, "--video", secret])
    with pytest.raises(SystemExit):
        client.main(["quickstart", "--base", BASE, "--video", "https://x.test/" + secret[9:30]])

    def echo(request):                                      # a hostile gateway echoing the key
        return httpx.Response(400, headers={"inference-id": secret[9:40]},
                              json={"error": {"code": secret[9:40].lower()}})
    _, rows, state = sweep(tmp_path, echo, "--concurrency", "1")
    assert secret[9:17] not in open(state).read() + capsys.readouterr().out
    assert rows[0]["error_code"] == "unrecognized"
