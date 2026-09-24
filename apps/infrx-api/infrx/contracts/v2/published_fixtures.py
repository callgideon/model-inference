"""F2C.c fixtures: Marlin as deployed, its serving profile, the refusal/violation cases and
the alias compatibility table. Generated from the records, never hand-edited:

    uv run --frozen python -m infrx.contracts.v2.published_fixtures --write

`tests/contracts/v2/test_published_model.py` regenerates them in memory and compares bytes;
the console half reads the same files (`apps/app/tests/contracts/v2/published-model.test.ts`).
They live in `published/` beside this module because `contracts/fixtures/` is guarded to
hold exactly the F2P base (`tests/contracts/test_fixtures.py`); folding them in is the
coordinator's wiring request.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from .. import codec, errors, limits, records as v1
from . import fixtures as v2fix
from . import published_model as pm
from .records import AccountingRegime

DIR = pathlib.Path(__file__).resolve().parent / "published"

# --- the deployed profile (release bda1586; handoff 20 §14.1) ---------------------------
# `/etc/marlin2b-gateway.env` sets MAX_VIDEO_SECONDS=82 (P-20: the engine's encoder-cache
# ceiling); every other value is the contracts default.
DEPLOYED_LIMITS = limits.PilotSettings(max_video_seconds=82.0)
# `gateway/routes/validate.py` SUPPORTED / UNSUPPORTED, restated because contracts never
# import the gateway; the test holds them equal.
ACCEPTED_PARAMETERS = ("frequency_penalty", "max_completion_tokens", "max_tokens", "messages",
                       "model", "n", "presence_penalty", "seed", "stop", "stream",
                       "temperature", "top_p")
REFUSED_PARAMETERS = ("function_call", "functions", "logit_bias", "logprobs",
                      "parallel_tool_calls", "price_snapshot", "response_format",
                      "tool_choice", "tools", "top_logprobs")
# What both `config.DEFAULT_ALLOWED_VIDEO_MIME` (the validator) and
# `media.prepare.MediaProfile` (preparation) accept: a type only one side accepts is not
# served (S2M §2.5 also lists video/mpeg, which neither accepts now).
SERVED_VIDEO_MIME = ("video/mp4", "video/quicktime", "video/webm")
# `config.Settings.fps/min_frames/max_frames/px_per_frame` (S2M §1.5, path A).
VIDEO_SAMPLING = {"fps": 2, "min_frames": 4, "max_frames": 240, "max_pixels_per_frame": 200704}
MODES = ("sync", "stream", "async")

# The hosted pilot's USD price rows (names and rates only; session-02 lines 895 and 903,
# handoff 20 §14.1): W7c keyed the unlabelled spelling, W7e the labelled one, both at the
# `public.models` rates. `infrx.price_versions` rows are immutable.
HOSTED_USD_ROWS = (
    {"price_version": "pv_marlin2b_usd_2026_09", "model_revision": v2fix.PUBLIC_MODEL_ID,
     "input_rate_per_million": "0.10000000", "output_rate_per_million": "0.30000000",
     "token_rules_version": "tr-1", "source": "rollout W7c"},
    {"price_version": "pv_marlin2b_usd_2026_09_r1", "model_revision": v2fix.REQUESTED_MODEL,
     "input_rate_per_million": "0.10000000", "output_rate_per_million": "0.30000000",
     "token_rules_version": "tr-1", "source": "rollout W7e"},
)
AS_OF = v2fix.T1
OWNED_BY = "nemostation"


def deployed_profile() -> pm.ServingProfile:
    return pm.serving_profile(
        limits=DEPLOYED_LIMITS, deployment=v2fix.BUILDERS["deployment_revision_public.json"](),
        serving=v2fix.BUILDERS["serving_revision.json"](), parameters=ACCEPTED_PARAMETERS,
        refused=REFUSED_PARAMETERS, video_mime=SERVED_VIDEO_MIME, execution_modes=MODES,
        **VIDEO_SAMPLING)


def _canonical_usd_row() -> v1.PriceSnapshot:
    row = next(r for r in HOSTED_USD_ROWS if r["model_revision"] == v2fix.REQUESTED_MODEL)
    return v1.PriceSnapshot(**{k: v for k, v in row.items() if k != "source"},
                            captured_at=datetime(2026, 9, 24, tzinfo=timezone.utc))


def published(regime: str) -> pm.PublishedModel:
    """Marlin as the deployed release would publish it, in either regime."""
    profile = deployed_profile()
    credit = regime == AccountingRegime.credit
    return pm.project(
        serving=v2fix.BUILDERS["serving_revision.json"](),
        deployment=v2fix.BUILDERS["deployment_revision_public.json"](), listing_version=1,
        regime=regime, credit_card=v2fix.BUILDERS["rate_card_marlin.json"]() if credit else None,
        credit_provisional=True, usd_price=_canonical_usd_row(),
        capability=profile.capability, retention=profile.retention, owned_by=OWNED_BY,
        available=True, as_of=AS_OF)


# --- refusal and violation cases (both languages apply the same patches) -------------
# A case is a list of `[path, value]` patches applied to the credit-regime projection;
# a `null` value deletes the key. `refusals`: the patched record must not parse.
# `violations`: it parses, and `violations(record, deployed_profile())` names exactly
# `expected` (an empty list: honest).
_TOOLS_ADVERTISED = [(["capability", "parameters"], sorted([*ACCEPTED_PARAMETERS, "tools"])),
                     (["capability", "unsupported_parameters"],
                      [p for p in REFUSED_PARAMETERS if p != "tools"])]
REFUSALS = (
    ("zero data retention claimed", [(["retention", "zero_data_retention"], True)]),
    ("trace-off claimed as deletion",
     [(["retention", "capture_off_deletes_serving_content"], True)]),
    ("an unknown video feature", [(["capability", "video", "native_streaming"], True)]),
    ("an unknown tool feature", [(["capability", "tools"], True)]),
    ("USD parsed as CREDIT: unit", [(["pricing", "credit", "unit"], "USD")]),
    ("USD parsed as CREDIT: a USD block in the credit slot",
     [(["pricing", "credit"], {"schema_version": 2, "price_version": "pv_marlin2b_usd_2026_09_r1",
                               "unit": "USD", "model_revision": v2fix.REQUESTED_MODEL,
                               "input_rate_per_million": "0.10000000",
                               "output_rate_per_million": "0.30000000",
                               "token_rules_version": "tr-1"})]),
    ("CREDIT parsed as USD: unit", [(["pricing", "legacy_usd", "unit"], "CREDIT")]),
    ("an amount that is not canonical",
     [(["pricing", "credit", "input_rate_per_million"], "400")]),
    ("an amount as a JSON number", [(["pricing", "credit", "input_rate_per_million"], 400)]),
    ("unpriced credit regime", [(["pricing", "credit"], None)]),
    ("a CREDIT rate shown in the legacy regime", [(["pricing", "regime"], "legacy_usd")]),
    ("USD keyed by a spelling, not the revision",
     [(["pricing", "legacy_usd", "model_revision"], v2fix.PUBLIC_MODEL_ID)]),
    ("an alias that resolves elsewhere",
     [(["aliases"], ["marlin2b", v2fix.PUBLIC_MODEL_ID, v2fix.REQUESTED_MODEL])]),
    ("a revision that is not <id>@<label>",
     [(["model_revision"], "nemostation/marlin-2b@latest")]),
    ("a cap as a string", [(["capability", "video", "max_seconds"], "82")]),
    ("a flag as a number", [(["capability", "video", "live_stream"], 0)]),
    ("a parameter both accepted and refused",
     [(["capability", "unsupported_parameters"], ["tools", "top_p"])]),
    ("video input without video limits", [(["capability", "video"], None)]),
    ("more input and output than the context", [(["capability", "max_output_tokens"], 4096)]),
    ("a missing required field", [(["retention", "result_ttl_s"], None)]),
    ("an unsorted allow-list", [(["capability", "parameters"], ["top_p", "model", "messages"])]),
    ("an availability outside the vocabulary", [(["availability"], "ready")]),
    ("a v1 schema version", [(["schema_version"], 1)]),
)
VIOLATIONS = (
    ("the stale 120 s video cap", [(["capability", "video", "max_seconds"], 120)],
     ["capability.video.max_seconds"]),
    ("tool calling advertised", _TOOLS_ADVERTISED, ["capability.parameters"]),
    ("native live-video input advertised", [(["capability", "video", "live_stream"], True)],
     ["capability.video.live_stream"]),
    ("a MIME type preparation refuses",
     [(["capability", "video", "mime_types"], sorted([*SERVED_VIDEO_MIME, "video/mpeg"]))],
     ["capability.video.mime_types"]),
    ("a refusal the deployment does not make",
     [(["capability", "parameters"], [p for p in ACCEPTED_PARAMETERS if p != "seed"]),
      (["capability", "unsupported_parameters"], sorted([*REFUSED_PARAMETERS, "seed"]))],
     ["capability.unsupported_parameters"]),
    ("a longer result lifetime than enforced", [(["retention", "result_ttl_s"], 172800)],
     ["retention.result_ttl_s"]),
    ("a shorter result lifetime than enforced", [(["retention", "result_ttl_s"], 3600)],
     ["retention.result_ttl_s"]),
    ("a physical deletion bound nobody committed",
     [(["retention", "physical_deletion_bound_s"], 86400)],
     ["retention.physical_deletion_bound_s"]),
    ("a different sampling rate", [(["capability", "video", "fps"], 1)],
     ["capability.video.fps"]),
    ("more output than the deployment allows",
     [(["capability", "max_input_tokens"], 28672), (["capability", "max_output_tokens"], 4096)],
     ["capability.max_output_tokens"]),
    ("a lower cap than enforced is honest", [(["capability", "video", "max_seconds"], 60)], []),
    ("a refusal left unnamed is not a claim",
     [(["capability", "unsupported_parameters"], [])], []),
)


def apply_patches(document: Any, patches: list) -> Any:
    """A copy of `document` with each `[path, value]` set; a `None` value deletes."""
    doc = json.loads(json.dumps(document))
    for path, value in patches:
        node = doc
        for key in path[:-1]:
            node = node[key]
        if value is None:
            node.pop(path[-1], None)
        else:
            node[path[-1]] = value
    return doc


def _cases() -> dict[str, Any]:
    def patches(items):
        return [[list(path), value] for path, value in items]
    return {
        "base": "published_marlin_credit.json",
        "profile": "serving_profile_marlin.json",
        "refusals": [{"name": n, "patches": patches(p)} for n, p in REFUSALS],
        "violations": [{"name": n, "patches": patches(p), "expected": e}
                       for n, p, e in VIOLATIONS],
    }


# --- the alias compatibility table (P-22) ------------------------------------------
# Every model string found in code, fixtures and results, with what the hosted pilot did
# with it at release bda1586 (legacy_usd regime; the catalog holds the operator seed) and
# where it was found. `before` is recorded evidence, not recomputed; `credit` and
# `legacy_usd` are `price()` under the amended rule, and the test holds them to `before`.
_HOSTED = "c0000004-0000-4000-8000-000000000004"
ALIASES = (
    (v2fix.PUBLIC_MODEL_ID,
     ["infrx/config.py MODEL_ID default (an omitted model resolves as this)",
      "supabase/migrations/0002_seed_models.sql id/served_model/snippets",
      "openrouter/provider-models.json id",
      "infrx/state/seed_marlin_provisional.sql catalog_listings.public_model_id",
      "infra/runbooks/rollout.md W7c"],
     {"deployment_revision_id": _HOSTED, "price_version": "pv_marlin2b_usd_2026_09"}),
    (v2fix.REQUESTED_MODEL,
     ["contracts fixtures v1/v2 (R62 pin, REQUESTED_MODEL)",
      "research/plan/evidence/e/E4B-endpoint.md examples",
      "operations.service.marlin_release / certify target",
      "models/marlin2b/results E1B-box-*/bench.jsonl and E4B-box-4226315 (bench --model)",
      "infra/runbooks/rollout.md W7e"],
     {"deployment_revision_id": _HOSTED, "price_version": "pv_marlin2b_usd_2026_09_r1"}),
    (v2fix.DEV_REQUESTED_MODEL,
     ["contracts/v2/fixtures.py DEV_REQUESTED_MODEL (private dev endpoint)"],
     {"refused": "not_found"}),
    ("nemostation/marlin-2b-dev",
     ["R101 private naming <provider slug>/<endpoint name>-<env> (state/catalog.py _PRIVATE)"],
     {"refused": "not_found"}),
    ("marlin2b",
     ["models/marlin2b/bench.py and smoke.py --model default (vLLM --served-model-name)",
      "models/marlin2b/results/bench.jsonl (target direct only)"],
     {"refused": "not_found"}),
    ("NemoStation/Marlin-2B",
     ["serving model_repo (Hugging Face id)", "research/matrix/pairs.json"],
     {"refused": "not_found"}),
    ("marlin-2b@2026-09-01",
     ["console view-model tests and traces/query.ts comment (R62 unprefixed form)"],
     {"refused": "not_found"}),
    ("nemostation/marlin-2b@tokcost",
     ["tests/w/test_prep_worker.py (an unpublished label)"],
     {"refused": "not_found"}),
    ("deepseek-ai/DeepSeek-V4.1-Flash", ["0002_seed_models.sql coming_soon row"],
     {"refused": "not_found"}),
    ("Qwen/Qwen3.8-27B", ["0002_seed_models.sql coming_soon row"], {"refused": "not_found"}),
    ("moonshotai/Kimi-K3", ["0002_seed_models.sql coming_soon row"], {"refused": "not_found"}),
)


def _priced(requested: str, model: pm.PublishedModel) -> dict[str, Any]:
    try:
        return codec.canonical_obj(pm.price(requested, [model]))
    except errors.DomainError as refusal:
        return {"refused": refusal.code}


def _aliases() -> dict[str, Any]:
    credit, legacy = published("credit"), published("legacy_usd")
    return {
        "hosted_usd_rows": list(HOSTED_USD_ROWS),
        "cases": [{"requested": requested, "found_in": found, "before": before,
                   "credit": _priced(requested, credit),
                   "legacy_usd": _priced(requested, legacy)}
                  for requested, found, before in ALIASES],
    }


BUILDERS = {
    "published_marlin_credit.json": lambda: published("credit"),
    "published_marlin_legacy_usd.json": lambda: published("legacy_usd"),
    "serving_profile_marlin.json": deployed_profile,
    "cases.json": _cases,
    "alias_compatibility.json": _aliases,
}


def names() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in DIR.glob("*.json")))


def load(name: str) -> Any:
    return json.loads((DIR / name).read_bytes())


def build() -> dict[str, bytes]:
    return {name: codec.canonical_bytes(builder()) for name, builder in BUILDERS.items()}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="regenerate the F2C.c published-model fixtures")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    changed = [n for n, b in build().items()
               if not (DIR / n).exists() or (DIR / n).read_bytes() != b]
    if args.write:
        for name in changed:
            (DIR / name).write_bytes(build()[name])
    print(("wrote " if args.write else "would change ") + (", ".join(changed) or "nothing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
