#!/usr/bin/env python3
"""API-AUTH / S2M §3 (G1R item 5): the OpenAI-style headless client against the revised
ingress, in process (httpx.ASGITransport), using only what is served.

`client_example.py` is G6B's quickstart and dataset recipe; it sends exactly the declared
surface (marlin-sop §2.2-2.3): `model`, `max_tokens`, `temperature`, one text and one
`video_url` part, over the `https` and `data:` forms. The `infrx-upload:` form is specified,
not served (G4U/M3), so it is not smoked; `--respond-async` is smoked against G3's jobs routes
in `tests/g/jobs`.
The acceptor is `credit.CreditStore` answering a sync completion the way G2 will.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import httpx
from fastapi.responses import JSONResponse

from infrx.contracts.conformance.v2_fakes import fake_v2_harness
from infrx.gateway.routes import validate
from infrx.operations import service

from . import credit, support
from .test_admission import CONSUMER_ROW

API_DIR = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("client_example", API_DIR / "client_example.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)

BASE = "http://gateway.test/v1"


def served(max_active=64):
    """The revised ingress over `credit.CreditStore`, answering a sync completion the way
    G2 will once admission has accepted the job. Returns (app, store, requests seen)."""
    store = credit.CreditStore(catalog=support.catalog(), wallets=fake_v2_harness().wallets,
                               keys={CONSUMER_ROW["id"]: dict(CONSUMER_ROW)},
                               max_active=max_active)
    sent, admit = [], credit.acceptor(store)

    async def accept(auth, request, idem):
        sent.append(request)
        await admit(auth, request, idem)
        return JSONResponse({"object": "chat.completion", "model": request.model_revision,
                             "choices": [{"index": 0, "finish_reason": "stop", "message": {
                                 "role": "assistant", "content": "a bus passes"}}],
                             "usage": {"prompt_tokens": 12, "completion_tokens": 4,
                                       "total_tokens": 16}})

    app, _ = support.cutover_app(sb=support.supabase(rows=(CONSUMER_ROW,)),
                                 ingress_deps=support.deps(accept=accept, catalog=store.catalog))
    return app, store, sent


def run(argv, app, monkeypatch, capsys):
    monkeypatch.setenv("INFRX_API_KEY", service.new_secret())
    monkeypatch.delenv("MARLIN_API_KEY", raising=False)
    code = client.main(argv, transport=httpx.ASGITransport(app=app))
    return code, capsys.readouterr().out


def declared(request):
    """Only the served surface: the closed parameter set and the two part types."""
    assert set(request.parameters) | {"model", "messages"} <= validate.SUPPORTED
    parts = [part["type"] for m in request.messages for part in m["content"]]
    assert sorted(parts) == ["text", "video_url"], parts


def test_api_auth__the_headless_quickstart_is_served_over_both_media_forms(
        tmp_path, monkeypatch, capsys):
    """https and inline `data:` video, sync JSON: the client reports `done` with the
    usage it was answered, and the store admitted exactly one CREDIT job per call at the
    pinned card."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    for form, video in (("url", "https://cdn.test/clip.mp4"), ("data", str(clip))):
        app, store, sent = served()
        code, out = run(["quickstart", "--base", BASE, "--form", form, "--video", video],
                        app, monkeypatch, capsys)
        row = json.loads(out)
        assert (code, row["status"], row["completion_tokens"]) == (0, "done", 4), (form, out)
        (request,) = sent
        declared(request)
        assert request.model_revision == client.MODEL
        (admission,) = store.jobs.values()
        assert admission.pins.rate_card_version == support.v2fix.RATE_CARD_VERSION


def test_dur_cap__the_headless_client_retries_denied_capacity_with_its_own_key(
        tmp_path, monkeypatch, capsys):
    """marlin-sop §3.6: a 429 is waited out for the `Retry-After` the ingress sent, then
    the same item is re-sent under the same `Idempotency-Key`, and admitted once."""
    app, store, sent = served(max_active=0)
    slept = []

    async def wait(seconds):
        slept.append(seconds)
        store.max_active = 64                        # capacity frees while it waits

    monkeypatch.setattr(client, "SLEEP", wait)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    item = {"dataset_version": "v1", "source_id": "cam-1", "episode_id": "e1",
            "segment_index": 0, "start_s": 0, "end_s": 60, "prompt": "describe",
            "prompt_version": "p1", "profile_version": "v1", "video": str(clip)}
    manifest = tmp_path / "items.jsonl"
    manifest.write_text(json.dumps(item) + "\n")
    code, out = run(["sweep", "--base", BASE, "--manifest", str(manifest), "--state",
                     str(tmp_path / "state.jsonl"), "--concurrency", "1"],
                    app, monkeypatch, capsys)
    assert code == 0, out
    assert slept == [5.0]
    assert len(sent) == 2 and sent[0].payload_digest == sent[1].payload_digest
    assert len(store.jobs) == 1
    ((org, operation, key),) = store.replays
    assert (org, operation) == (CONSUMER_ROW["org_id"], "chat.completions")
    assert key.startswith("sop1.")
