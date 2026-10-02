"""G7: public capability discovery - `GET /v1/models` is one projection of what the release
enforces (RV-01, CATALOG-TRUTH).

The route used to return `openrouter/provider-models.json` verbatim: 120 s clips, `tools`,
a concurrency of 16, `is_ready` and `compliance.zdr: true`, none of which the runtime
honours. Each entry is now F2C.c's `PublishedModel`, built by `published_model.project`
from:

* the trusted catalog rows admission resolves (`rt.ingress.catalog`, a consumer's view of
  the served model `MODEL_ID`): deployment, serving revision and the card - in the CREDIT
  regime only the card this deployment was approved to serve (`ACTIVE_RATE_CARD_VERSION`,
  S3 F11), in the legacy regime the USD price identity keyed by the canonical revision
  (P-22) when the catalog can read one (`usd_price`, D10's seam);
* the enforced profile (`published_model.serving_profile`) from the values the admission
  path itself reads: the validator's allow-list, the pilot limits, the MIME types both
  the validator and the media profile accept, the sampling settings, and the modes this
  process serves (`stream` if the serving revision declares SSE output, `async` once the
  jobs router installed its hook on the relay);
* availability from the ingress's readiness probes, never a flag in a file.

An entry is published only if `project` accepts it against the enforced profile AND it
passes `violations` against the approved release profile (`APPROVED`: the deployed Marlin
profile, 82 s - P-20/P-23). Anything else - unpriced, retired, private, an unapproved card, a runtime past
the approved profile - publishes nothing. `provider_document` renders the same entry as the
OpenRouter v2.4 provider document (OpenRouter is deferred: nothing serves it yet).

`release_violations`/`assert_release_profile` are the settings-only half of the gate, for
the composition root's startup refusal (`app.create_app`, pilot mode; the code default
`MAX_VIDEO_SECONDS` is this release's 82).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import Request
from fastapi.responses import JSONResponse

from ...config import RuntimeMisconfigured
from ...contracts import errors, wire
from ...contracts.limits import env_name
from ...contracts.records import ExecutionMode
from ...contracts.v2 import published_model as pm
from ...contracts.v2.published_fixtures import deployed_profile
from ...contracts.v2.records import CredentialAudience
from ...media.prepare import MediaProfile
from ...operations.transition import unapproved
from . import intake, validate
from .catalog import CALLABLE
from .ingress import OK, component_state
from .relay import CREDIT, _dependency

log = logging.getLogger("infrx.gateway")

MODELS_PATH = "/v1/models"
OWNED_BY = "nemostation"
# The approved release profile: Marlin-2B as release bda1586 deploys it (F2C.c's record of
# `/etc/marlin2b-gateway.env`: 82 s). A runtime past it advertises nothing.
APPROVED = deployed_profile()
# `publish`'s route for the served model: the gateway's own engine and its probes.
SERVED = "served"
# E3L-F1 fallback only: a catalog with no `listing_version` reader (an adapter this lane
# does not own) reports this instead of crashing. The real and fake directories both
# implement it now, so a served model normally reports the listing it actually resolved.
_UNKNOWN_LISTING_VERSION = 1
# The OpenRouter v2.4 names of the parameters it types, with their bounds (validate's).
PROVIDER_PARAMETERS = {
    "max_tokens": ("max_tokens", {"type": "integer", "min": 1}),
    "temperature": ("temperature", {"type": "range", "min": 0, "max": 2}),
    "top_p": ("top_p", {"type": "range", "min": 0, "max": 1}),
    "stop": ("stop_sequences", {"type": "array", "max_items": validate.MAX_STOP_SEQUENCES}),
    "seed": ("seed", {"type": "integer"}),
    "presence_penalty": ("presence_penalty", {"type": "range", "min": -2, "max": 2}),
    "frequency_penalty": ("frequency_penalty", {"type": "range", "min": -2, "max": 2})}


def admission_concurrency(limits) -> int:
    """How many jobs one key can have admitted at once: the store's per-key, per-org and
    total limits, whichever binds first (`ports.JobStore.admit`)."""
    return min(limits.max_active_jobs_per_key, limits.max_active_jobs_per_org,
               limits.max_active_jobs)


def release_violations(settings) -> list[str]:
    """Each setting that takes the runtime past the approved release profile, named by its
    environment variable. Caps and retention may be tighter, never looser."""
    pilot, capability = settings.pilot, APPROVED.capability
    video, retention = capability.video, APPROVED.retention
    caps = (("max_video_seconds", video.max_seconds), ("max_media_bytes", video.max_bytes),
            ("max_request_bytes", capability.max_request_bytes),
            ("max_output_tokens", capability.max_output_tokens),
            ("max_context_tokens", capability.max_context_tokens),
            ("result_ttl_s", retention.result_ttl_s),
            ("journal_chunk_ttl_s", retention.stream_journal_ttl_s),
            ("idempotency_ttl_s", retention.idempotency_ttl_s),
            ("processing_cache_ttl_s", retention.processing_cache_ttl_s))
    found = [f"{env_name(name)}: {getattr(pilot, name):g} > approved {bound}"
             for name, bound in caps if getattr(pilot, name) > bound]
    extra = sorted(set(settings.allowed_video_mime) - set(video.mime_types))
    if extra:
        found.append(f"ALLOWED_VIDEO_MIME: {','.join(extra)} not approved")
    return found


def assert_release_profile(settings) -> None:
    """The startup refusal: the composition root's to call (wiring request)."""
    found = release_violations(settings)
    if found:
        raise RuntimeMisconfigured(settings.pilot.infrx_mode, detail=(
            "past the approved release profile: " + "; ".join(found)))


def served_modes(rt, serving) -> list[ExecutionMode]:
    modes = [ExecutionMode.sync]
    if serving.capability.stream_output:
        modes.append(ExecutionMode.stream)
    if getattr(getattr(rt, "relay", None), "on_async", None) is not None:
        modes.append(ExecutionMode.async_)            # the jobs router is mounted (G3)
    return modes


def enforced_profile(rt, deployment, serving) -> pm.ServingProfile:
    """What admission enforces for this deployment, from the values it reads."""
    settings = rt.settings
    return pm.serving_profile(
        limits=settings.pilot, deployment=deployment, serving=serving,
        parameters=validate.SUPPORTED, refused=validate.UNSUPPORTED,
        video_mime=set(settings.allowed_video_mime) & set(MediaProfile().allowed_mime),
        fps=int(settings.fps), min_frames=settings.min_frames, max_frames=settings.max_frames,
        max_pixels_per_frame=settings.px_per_frame, execution_modes=served_modes(rt, serving))


async def catalog_rows(rt, model_id: str | None = None):
    """The served model (or `model_id`) as a consumer's key resolves it now: (deployment,
    serving, card, USD price, listing version), or None. A catalog that cannot answer is a
    retryable 503, never an empty list or a stale claim."""
    catalog, settings = rt.ingress.catalog, rt.settings
    deployment = await _dependency(catalog.resolve(
        model_id or settings.model_id, audience=CredentialAudience.consumer, endpoint_id=None))
    if deployment is None:
        return None
    serving = await _dependency(catalog.serving_revision(deployment.serving_version_id))
    card = await _dependency(catalog.active_rate_card(deployment.deployment_revision_id))
    if serving is None or card is None:
        return None
    reader = getattr(catalog, "usd_price", None)
    usd = await _dependency(reader(serving.model_revision)) if reader is not None else None
    lv_reader = getattr(catalog, "listing_version", None)
    listing_version = _UNKNOWN_LISTING_VERSION
    if lv_reader is not None:
        resolved = await _dependency(lv_reader(deployment.deployment_revision_id))
        if resolved is not None:
            listing_version = resolved
    return deployment, serving, card, usd, listing_version


def provisional(card) -> bool:
    """A card is provisional unless its approval record is an operator approval (P-01),
    judged by G8's own rule (`transition.unapproved`, the check `publish-card` and the
    CREDIT transition refuse by): an absent, blank or marked approval is provisional (fail
    closed - a provisional rate is never published as approved). ponytail: the record
    carries `approved_by` only; D10 writes `rate_card_versions.provisional` from the same
    text, so reading the column too adds no case today."""
    return unapproved(card.approved_by) is not None


def publish(rt, rows, now: datetime, route: str | None = SERVED) -> list[pm.PublishedModel]:
    """The published entry of `rows`, or nothing: unpriced, not this deployment's card,
    not publishable (`project`'s refusal) or past a profile. `route` is `route_state`'s:
    the served model is the gateway's own engine (`SERVED`: today's entry, byte for byte);
    another alias needs a route serving it - none hides it, a down one is `unavailable`."""
    if rows is None or route is None:
        return []
    deployment, serving, card, usd, listing_version = rows
    settings = rt.settings
    regime = settings.deployment.accounting_regime
    served = route == SERVED
    if served and regime == CREDIT \
            and card.rate_card_version != settings.pilot.active_rate_card_version:
        return []                                     # not the card this deployment serves
    if not served and provisional(card):
        return []                     # another alias: only an operator-approved card lists it
    profile = enforced_profile(rt, deployment, serving)
    available = all(state == OK for state in component_state(rt.ingress.checks).values()) \
        and route in (SERVED, "ready")
    try:
        published = pm.project(
            serving=serving, deployment=deployment, listing_version=listing_version,
            regime=regime, credit_card=card, credit_provisional=provisional(card), usd_price=usd,
            capability=profile.capability, profile=profile,
            owned_by=OWNED_BY if served else serving.public_model_id.partition("/")[0],
            available=available, as_of=now)
    except (errors.NotFound, errors.InvalidRequest):
        return []            # project's refusal: unpublishable, or past the enforced profile
    overclaims = pm.violations(published, APPROVED)
    if overclaims:
        log.error("%s is not published: %s", serving.model_revision, "; ".join(overclaims))
        return []
    return [published]


async def listed_rows(rt) -> list:
    """AP-06 06c: what discovery considers - the served model first (today's document), then
    each other alias the registry lists (`published_aliases`, the catalog's reader - absent,
    the served model alone), as `(rows, served)` pairs."""
    found = [(await catalog_rows(rt), True)]
    reader = getattr(rt.ingress.catalog, "published_aliases", None)
    for alias in (await _dependency(reader())) if reader is not None else ():
        if alias != rt.settings.model_id:
            found.append((await catalog_rows(rt, alias), False))
    return found


async def route_state(rt, rows, served: bool) -> str | None:
    """`SERVED` for the gateway's own model; for another alias what `rt.routes` (an engine
    route serving exactly this pinned revision: AP-05's endpoints, a wiring request) says -
    "ready", "unavailable" (also when the route table cannot answer: explicit, never hidden
    as healthy) - or None: no route, not listed. Without a route table only the served model
    is routable: the relay's one upstream serves Marlin and nothing else."""
    if served:
        return SERVED
    routes = getattr(rt, "routes", None)
    if routes is None or rows is None:
        return None
    try:
        return await routes.state(rows[0], rows[1])
    except Exception:                     # noqa: BLE001 - an outage is unavailable, not absent
        log.warning("route table unavailable for %s", rows[1].public_model_id)
        return "unavailable"


def price_check(catalog, settings):
    """The `price_source` readiness probe `pilot.build_ingress_deps` composes (G7 WR-2):
    the served model resolves for a consumer, callable, with a CREDIT card (the ingress's
    resolution needs one in both regimes, R69) - and priced in the regime
    admission charges: the approved card in CREDIT, the USD identity of the canonical
    revision in legacy (P-22). The retired `pilot.price_check` asked for a card in both
    regimes, so a legacy pilot with no USD row read ready."""
    async def check() -> bool:
        deployment = await catalog.resolve(settings.model_id,
                                           audience=CredentialAudience.consumer,
                                           endpoint_id=None)
        if deployment is None or (deployment.visibility, deployment.state) \
                != CALLABLE[CredentialAudience.consumer]:
            return False
        card = await catalog.active_rate_card(deployment.deployment_revision_id)
        if card is None:
            return False
        if settings.deployment.accounting_regime == CREDIT:
            return card.rate_card_version == settings.pilot.active_rate_card_version
        serving = await catalog.serving_revision(deployment.serving_version_id)
        reader = getattr(catalog, "usd_price", None)
        return serving is not None and reader is not None \
            and await reader(serving.model_revision) is not None
    return check


def provider_document(entry: dict, concurrency: int) -> dict:
    """The OpenRouter v2.4 provider document of one published entry. Only what the entry
    states: USD prices only from its USD price identity - none in the CREDIT regime, where
    no conversion is allowed (a product decision, F2C.c open issue) - and no measured
    capacity (P-18), only the admission `concurrency` one key gets."""
    capability, pricing = entry["capability"], entry["pricing"]
    usd = pricing.get("legacy_usd") if pricing["regime"] != CREDIT else None

    def priced(kind: str, rate: str) -> dict:
        if usd is None:
            return {}
        per_token = (Decimal(usd[rate]) / 1_000_000).normalize()
        return {"pricing": [{"type": kind, "unit": "token", "cost_usd": f"{per_token:f}"}]}

    inputs = [{"type": "text", "supported_inputs": {
        "max_context_length": {"value": capability["max_context_tokens"], "unit": "token"},
        "max_prompt_length": {"value": capability["max_input_tokens"], "unit": "token"}},
        **priced("prompt", "input_rate_per_million")}]
    if capability.get("video"):
        inputs.append({"type": "video", "supported_inputs": {"max_prompt_length": {
            "value": capability["video"]["max_seconds"], "unit": "second"}},
            **priced("prompt", "input_rate_per_million")})
    typed = {name: dict(spec) for ours, (name, spec) in PROVIDER_PARAMETERS.items()
             if ours in capability["parameters"]}
    if "max_tokens" in typed:
        typed["max_tokens"]["max"] = capability["max_output_tokens"]
    output = {"type": "text",
              "max_length": {"value": capability["max_output_tokens"], "unit": "token"},
              "streaming": ExecutionMode.stream.value in capability["execution_modes"],
              "supported_parameters": typed, **priced("completion", "output_rate_per_million")}
    return {"schema_version": "2.4", "id": entry["id"], "name": entry["model_revision"],
            "hugging_face_id": entry["serving"]["model_repo"], "created": entry["created"],
            "input_modalities": inputs, "output_modalities": [output],
            "capacity": [{"type": "concurrency", "unit": "request", "value": concurrency}],
            "is_ready": entry["availability"] == "available",
            "compliance": {"zdr": entry["retention"]["zero_data_retention"], "hipaa": False}}


def register(app, rt):
    """Mount `GET /v1/models` over the ingress's catalog and probes (`rt.ingress`). No key
    is asked for: discovery is public, and names only what a consumer key may call. The
    catalog rows are cached for `price_ttl` seconds (one refresh at a time), so an
    anonymous caller cannot turn discovery into database load; availability is live."""
    guarded = intake.guard(rt.ingress.new_request_id)
    cache = {"at": None, "rows": None}
    refreshing = asyncio.Lock()

    async def rows():
        async with refreshing:
            at = cache["at"]
            if at is None or not 0 <= rt.clock() - at < rt.settings.price_ttl:
                cache["rows"] = await listed_rows(rt)
                cache["at"] = rt.clock()
            return cache["rows"]

    @app.get(MODELS_PATH)
    @guarded
    async def list_models(request: Request, request_id: str):
        now = datetime.fromtimestamp(rt.clock(), timezone.utc)
        data = [entry.model_dump(mode="json") for found, served in await rows()
                for entry in publish(rt, found, now, await route_state(rt, found, served))]
        return JSONResponse({"object": "list", "data": data},
                            headers={wire.HEADER_INFERENCE_ID: request_id})

    list_models.__module__ = __name__
    return list_models
