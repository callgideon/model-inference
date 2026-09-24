#!/usr/bin/env python3
"""CATALOG-TRUTH (G7 item 1, RV-01): `/v1/models` is one projection of what the release
enforces, and every claim in it is checked against the mounted admission path.

The stale document (`openrouter/provider-models.json`, served verbatim) advertised 120 s
clips, a concurrency of 16, `tools`, readiness and zero data retention - none of which the
runtime honours. The failure oracle here is the admission path itself: a claim the
ingress, the media profile or the store refuses is a failing case, not review feedback.
"""
from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured
from infrx.contracts.conformance import builders as b
from infrx.contracts.v2 import published_model as pm
from infrx.contracts.v2.records import DeploymentState
from infrx.gateway.routes import ingress, models
from infrx.media.probe import Probed

from . import relay_support as rs, support
from .jobs import world as jw

IDS = support.IDS
# `/etc/marlin2b-gateway.env` (handoff 20 §14.1): the release's own settings.
DEPLOYED = {"max_video_seconds": 82.0}
CARD = "rc_marlin2b_2026_09_provisional"
MODELS_PATH = "/v1/models"
CREDIT = support.BUILD.replace(accounting_regime="credit")
# One valid value per parameter the discovery may advertise. An advertised parameter with
# no entry here is a claim nobody proved (`test_..._every_advertised_claim_...` fails).
VALID = {"stream": False, "max_tokens": 1, "max_completion_tokens": 1, "temperature": 0.5,
         "top_p": 0.5, "n": 1, "stop": "END", "seed": 7, "presence_penalty": 0.5,
         "frequency_penalty": 0.5}
# The legacy regime's USD price identity, keyed by the canonical revision (P-22).
USD = b.DEFAULT_PRICE


def priced(catalog=None, snapshot=USD):
    """The seeded catalog with D10's USD price reader (`usd_price(model_revision)`)."""
    catalog = catalog if catalog is not None else support.catalog()

    async def usd_price(model_revision):
        return snapshot if snapshot and model_revision == snapshot.model_revision else None

    catalog.usd_price = usd_price
    return catalog


def discovery_app(config=None, *, catalog=None, checks=None):
    """The composition order of `app.ROUTERS`: models, then the ingress, over one
    `rt.ingress` (the catalog admission resolves through) and a recording acceptor."""
    rt = support.runtime(config if config is not None else support.settings(**DEPLOYED))
    calls, accept = support.recorder()
    rt.ingress = support.deps(accept=accept, catalog=catalog if catalog is not None else priced(),
                              **({"checks": checks} if checks is not None else {}))
    app = FastAPI()
    app.state.runtime, rt.app = rt, app
    models.register(app, rt)
    ingress.register(app, rt)
    return TestClient(app), calls


def listed(tc) -> list[dict]:
    response = tc.get(MODELS_PATH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "list"
    return body["data"]


def only(tc) -> dict:
    data = listed(tc)
    assert len(data) == 1, f"one served model is published, not {data}"
    return data[0]


def pairs(value, path=()):
    """Every (path, leaf) in a JSON document: a stale claim anywhere is found."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from pairs(item, path + (key,))
    elif isinstance(value, list):
        for item in value:
            yield from pairs(item, path)
    else:
        yield path, value


# --- what is advertised -------------------------------------------------------------
def test_catalog_truth__discovery_makes_none_of_the_stale_claims():
    """RV-01's observed claims, each named: no ZDR, no 120 s, no concurrency 16, no tools,
    no native video streaming, no readiness flag from a file."""
    entry = only(discovery_app()[0])
    pm.PublishedModel.model_validate(entry)                  # F2C.c's closed record
    leaves = list(pairs(entry))
    assert not [p for p, v in leaves if p and p[-1] in ("zdr", "zero_data_retention")
                and v is not False], "a zero-data-retention claim"
    assert entry["retention"]["zero_data_retention"] is False
    assert entry["retention"]["capture_off_deletes_serving_content"] is False
    capability = entry["capability"]
    assert capability["video"]["max_seconds"] == 82
    assert not [p for p, v in leaves if v == 120], "a 120 anywhere"
    assert "tools" not in capability["parameters"]
    assert {"tools", "tool_choice", "response_format"} <= set(capability["unsupported_parameters"])
    assert capability["video"]["live_stream"] is False
    assert not [p for p, v in leaves if p and p[-1] == "concurrency" or v == 16]
    assert "is_ready" not in entry and "compliance" not in entry


def test_catalog_truth__discovery_is_the_catalog_resolution_admission_uses():
    """The entry is the served model as admission resolves it for a consumer key: the
    trusted rows, never a file. Unpriced, retired or an unapproved card: not listed."""
    catalog = priced()
    entry = only(discovery_app(catalog=catalog)[0])
    serving = catalog.servings[IDS.serving_version]
    assert (entry["id"], entry["model_revision"]) == (serving.public_model_id,
                                                      serving.model_revision)
    assert entry["aliases"] == sorted({serving.public_model_id, serving.model_revision})
    assert entry["deployment_revision_id"] == IDS.prod_deployment
    assert entry["serving"]["serving_version_id"] == serving.serving_version_id
    assert entry["created"] == int(serving.created_at.timestamp())
    assert entry["availability"] == "available"
    # No jobs router on this app: explicit async is not served, so not advertised.
    assert entry["capability"]["execution_modes"] == ["stream", "sync"]
    usd = entry["pricing"]["legacy_usd"]
    assert (entry["pricing"]["regime"], usd["price_version"], usd["model_revision"]) == (
        "legacy_usd", USD.price_version, serving.model_revision)
    assert entry["pricing"].get("credit") is None            # not charged, not shown
    # Unpriced (R69: no card; no USD identity for the legacy regime) and retired (R70).
    unpriced = priced()
    del unpriced.rate_cards[IDS.prod_deployment]
    assert listed(discovery_app(catalog=unpriced)[0]) == []
    assert listed(discovery_app(catalog=priced(snapshot=None))[0]) == []
    assert listed(discovery_app(catalog=support.catalog())[0]) == []     # no USD reader
    retired = priced()
    retired.deployments[IDS.prod_deployment] = retired.deployments[
        IDS.prod_deployment].model_copy(update={"state": DeploymentState.retired})
    assert listed(discovery_app(catalog=retired)[0]) == []


def test_catalog_truth__credit_discovery_advertises_only_the_approved_card():
    """F11: in the CREDIT regime the card advertised is the one this deployment was
    approved to serve (`ACTIVE_RATE_CARD_VERSION`); another card is not a price."""
    config = support.settings(deployment=CREDIT, active_rate_card_version=CARD, **DEPLOYED)
    entry = only(discovery_app(config)[0])
    assert entry["pricing"]["regime"] == "credit"
    assert entry["pricing"]["credit"]["rate_card_version"] == CARD
    assert entry["pricing"]["credit"]["unit"] == "CREDIT"
    other = support.settings(deployment=CREDIT, active_rate_card_version="rc_other", **DEPLOYED)
    assert listed(discovery_app(other)[0]) == []


def test_catalog_truth__an_unreachable_catalog_is_a_retryable_503_not_a_claim():
    catalog = priced()

    async def down(*a, **kw):
        raise ConnectionError("catalog at postgresql://u:pw@db is down")

    catalog.resolve = down
    response = discovery_app(catalog=catalog)[0].get(MODELS_PATH)
    assert response.status_code == 503, response.text
    assert support.error_of(response)["code"] == "dependency_unavailable"
    assert response.headers["Retry-After"]
    assert "pw@db" not in response.text


def test_catalog_truth__availability_follows_readiness_not_a_file():
    tc, _ = discovery_app(checks={"price_source": lambda: True, "journal": lambda: False},
                          config=support.settings("dev", **DEPLOYED))
    assert only(tc)["availability"] == "unavailable"


# --- the release profile gate -------------------------------------------------------
def test_catalog_truth__a_runtime_past_the_approved_release_profile_advertises_nothing():
    """The code default MAX_VIDEO_SECONDS=120 is profile v1's, not this release's (P-20:
    the engine's encoder cache refuses past 82 s). A runtime whose enforced profile exceeds
    the approved release profile publishes no model, in any mode; `release_violations`
    names the setting for the composition root's startup refusal (wiring request)."""
    stale = support.settings()                        # pilot, the code default 120
    assert models.release_violations(stale) == ["MAX_VIDEO_SECONDS: 120 > approved 82"]
    assert listed(discovery_app(stale)[0]) == []
    assert listed(discovery_app(support.settings("dev"))[0]) == []
    assert models.release_violations(support.settings(**DEPLOYED)) == []
    longer = support.settings(result_ttl_s=172_800.0, **DEPLOYED)
    assert models.release_violations(longer) == ["RESULT_TTL_S: 172800 > approved 86400"]
    assert listed(discovery_app(longer)[0]) == []
    mpeg = support.settings(**DEPLOYED)
    mpeg.allowed_video_mime = {"video/mp4", "video/mpeg"}
    assert models.release_violations(mpeg) == ["ALLOWED_VIDEO_MIME: video/mpeg not approved"]
    with pytest.raises(RuntimeMisconfigured, match="MAX_VIDEO_SECONDS"):
        models.assert_release_profile(stale)
    models.assert_release_profile(support.settings(**DEPLOYED))


def test_catalog_truth__v1_models_publishes_only_what_the_deployed_profile_admits():
    """F2C.c appendix A (the seam test handed to G7), with the route's dependency - the
    ingress's catalog - supplied: every entry parses as the closed record and passes
    `violations` against the profile built from the running settings."""
    from infrx.contracts import records as v1
    from infrx.contracts.v2 import fixtures as v2fix
    from infrx.gateway.routes import validate
    from infrx.media.prepare import MediaProfile

    rt = support.runtime(support.settings(max_video_seconds=82.0))
    rt.ingress = support.deps(catalog=priced())
    app = FastAPI()
    models.register(app, rt)
    body = TestClient(app).get("/v1/models").json()
    assert body["data"], "the catalog is empty"
    settings = rt.settings
    profile = pm.serving_profile(
        limits=settings.pilot, deployment=v2fix.BUILDERS["deployment_revision_public.json"](),
        serving=v2fix.BUILDERS["serving_revision.json"](), parameters=validate.SUPPORTED,
        refused=validate.UNSUPPORTED,
        video_mime=set(settings.allowed_video_mime) & set(MediaProfile().allowed_mime),
        fps=int(settings.fps), min_frames=settings.min_frames, max_frames=settings.max_frames,
        max_pixels_per_frame=settings.px_per_frame, execution_modes=list(v1.ExecutionMode))
    for entry in body["data"]:
        assert pm.violations(pm.PublishedModel.model_validate(entry), profile) == []


# --- the CI check: every claim against the mounted admission path -------------------
def accepted(tc, calls, body):
    """The ingress handed exactly this request to the acceptor."""
    before = len(calls)
    response = post(tc, body)
    assert response.status_code == 202, (body, response.text)
    assert len(calls) == before + 1


def refused(response, status, code, param=None):
    assert response.status_code == status, response.text
    error = support.error_of(response)
    assert error["code"] == code, error
    if param is not None:
        assert error.get("param") == param, error


def post(tc, body, **headers):
    return tc.post(support.CHAT_PATH, headers={**support.AUTH, **headers}, json=body)


def test_catalog_truth__every_advertised_claim_is_what_admission_enforces():
    """The rendered discovery against the mounted ingress: each advertised parameter and
    alias is accepted, each advertised refusal is refused by name, the output ceiling and
    the MIME list are exact, one video per request, the byte cap is the one intake uses."""
    tc, calls = discovery_app()
    entry = only(tc)
    capability = entry["capability"]
    text = [{"role": "user", "content": "hi"}]
    for alias in entry["aliases"]:
        accepted(tc, calls, {"model": alias, "messages": text})
    for name in capability["parameters"]:
        if name in ("model", "messages"):
            continue
        assert name in VALID, f"{name} is advertised and no case proves it is accepted"
        accepted(tc, calls, {"messages": text, name: VALID[name]})
    for name in capability["unsupported_parameters"]:
        refused(post(tc, {"messages": text, name: True}), 400, "unsupported_parameter", name)
    ceiling = capability["max_output_tokens"]
    accepted(tc, calls, {"messages": text, "max_tokens": ceiling})
    refused(post(tc, {"messages": text, "max_tokens": ceiling + 1}), 400, "invalid_request",
            "max_tokens")
    if "stream" in capability["execution_modes"]:
        accepted(tc, calls, {"messages": text, "stream": True})
    payload = base64.b64encode(b"\x00" * 16).decode()
    video = capability["video"]
    for mime in video["mime_types"]:
        clip = {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{payload}"}}
        accepted(tc, calls, {"messages": [{"role": "user", "content": [clip]}]})
    other = {"type": "video_url", "video_url": {"url": f"data:video/mpeg;base64,{payload}"}}
    refused(post(tc, {"messages": [{"role": "user", "content": [other]}]}), 400,
            "unsupported_media")
    clip = {"type": "video_url", "video_url": {"url": "https://media.example.com/a.mp4"}}
    content = [clip] * (video["max_per_request"] + 1)
    refused(post(tc, {"messages": [{"role": "user", "content": content}]}), 400,
            "unsupported_media")
    # The byte cap, at a size a test can send: the advertised number is intake's own.
    small, _ = discovery_app(support.settings(max_request_bytes=65_536, **DEPLOYED))
    cap = only(small)["capability"]["max_request_bytes"]
    assert cap == 65_536
    oversized = b'{"messages": []' + b" " * cap + b"}"
    refused(small.post(support.CHAT_PATH, headers=support.RAW, content=oversized), 413,
            "request_too_large")


def probing(seconds: float):
    async def probe(data: bytes) -> Probed:
        return Probed(mime="video/mp4", duration_s=seconds, width=640, height=360,
                      codec="h264")
    return probe


def world_with_discovery(monkeypatch, seconds: float = 4.0):
    """G3's world at the release's settings, with `/v1/models` mounted on its app."""
    config = support.settings(**DEPLOYED)
    monkeypatch.setattr(rs, "probe", probing(seconds))
    world = jw.JobsWorld(limits=config.pilot, config=config)
    priced(world.catalog)
    models.register(world.app, world.app.state.runtime)
    return world


def discover(world) -> dict:
    reply = rs.run(jw.send(world.app, "GET", MODELS_PATH))
    assert reply.status == 200, reply.body
    data = reply.json()["data"]
    assert len(data) == 1, f"one served model is published, not {data}"
    return data[0]


def test_catalog_truth__the_advertised_video_ceiling_is_the_one_preparation_enforces(
        monkeypatch):
    """The media profile preparation applies at acceptance, not the validator: a clip
    of exactly the advertised seconds is admitted, one a second longer is refused before
    any job exists (no hold, no engine work)."""
    world = world_with_discovery(monkeypatch, seconds=82.0)
    ceiling = discover(world)["capability"]["video"]["max_seconds"]
    assert ceiling == 82
    reply = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(rs.VIDEO)))
    assert reply.status == 202, reply.body
    world = world_with_discovery(monkeypatch, seconds=ceiling + 1.0)
    reply = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(rs.VIDEO)))
    assert reply.status == 400, reply.body
    assert reply.json()["error"]["code"] == "unsupported_media"
    assert world.jobs.jobs == {}


def test_catalog_truth__the_advertised_modes_and_concurrency_are_the_admission_limits(
        monkeypatch):
    """Explicit async is advertised because the jobs router serves it; the concurrency the
    provider document advertises is exactly how many jobs one key has admitted before a
    typed 429 with retry guidance (the store's per-key limit), never 16."""
    world = world_with_discovery(monkeypatch)
    entry = discover(world)
    assert entry["capability"]["execution_modes"] == ["async", "stream", "sync"]
    document = models.provider_document(
        entry, models.admission_concurrency(world.app.state.runtime.settings.pilot))
    (capacity,) = document["capacity"]
    limit = capacity["value"]
    assert limit == 8
    for index in range(limit):
        reply = rs.run(jw.send(world.app, "POST", "/v1/jobs",
                               body=rs.body(stop=f"s{index}")))
        assert reply.status == 202, (index, reply.body)
    reply = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(stop="over")))
    assert reply.status == 429, reply.body
    assert reply.json()["error"]["code"] == "capacity_exhausted"
    assert int(reply.headers["retry-after"]) > 0


def test_catalog_truth__a_mode_the_serving_revision_does_not_declare_is_not_advertised():
    catalog = priced()
    serving = catalog.servings[IDS.serving_version]
    catalog.servings[IDS.serving_version] = serving.model_copy(update={
        "capability": serving.capability.model_copy(update={"stream_output": False})})
    tc, _ = discovery_app(catalog=catalog)
    entry = only(tc)
    assert "stream" not in entry["capability"]["execution_modes"]
    refused(post(tc, {"messages": [{"role": "user", "content": "hi"}], "stream": True}),
            400, "unsupported_parameter", "stream")


# --- the OpenRouter provider document: the same projection, rendered -----------------
def test_catalog_truth__the_provider_document_is_rendered_from_the_projection():
    """OpenRouter (deferred) reads a v2.4 provider document; it is the same entry
    rendered, so it cannot drift: the ceiling, the parameters, output SSE, the admission
    concurrency, `compliance.zdr` false, and USD only from a USD price identity."""
    entry = only(discovery_app()[0])
    document = models.provider_document(entry, 8)
    (video,) = [m for m in document["input_modalities"] if m["type"] == "video"]
    assert video["supported_inputs"]["max_prompt_length"] == {"value": 82, "unit": "second"}
    (text_out,) = document["output_modalities"]
    assert text_out["streaming"] is True
    assert "tools" not in text_out["supported_parameters"]
    ours = {name: source for source, (name, _) in models.PROVIDER_PARAMETERS.items()}
    assert {ours[name] for name in text_out["supported_parameters"]} <= set(
        entry["capability"]["parameters"])
    assert text_out["supported_parameters"]["max_tokens"]["max"] == 2048
    assert document["capacity"] == [{"type": "concurrency", "unit": "request", "value": 8}]
    assert document["compliance"] == {"zdr": False, "hipaa": False}
    assert document["is_ready"] is True
    # USD per token, from the USD price identity only (0.20 per million = 2e-7).
    assert text_out["pricing"] == [{"type": "completion", "unit": "token",
                                    "cost_usd": "0.0000006"}]
    assert video["pricing"] == [{"type": "prompt", "unit": "token", "cost_usd": "0.0000002"}]
    assert not [p for p, v in pairs(document) if v == 120]
    # The CREDIT regime publishes no USD price: none may be derived from a CREDIT card.
    credit = only(discovery_app(support.settings(deployment=CREDIT,
                                                 active_rate_card_version=CARD, **DEPLOYED))[0])
    document = models.provider_document(credit, 8)
    assert not [p for p, v in pairs(document) if p and p[-1] == "cost_usd"]
