#!/usr/bin/env python3
"""OpenAI-compatible gateway in front of vLLM for Marlin-2B.

The implementation now lives in the `infrx` package (F1 extraction, behavior
unchanged); this module stays the deployment entry point — `uvicorn gateway:app`
— and keeps the old module-global names working, so the systemd unit, the README
and the existing tests need no change and a rollback is a plain revert.

What it adds on top of vLLM's own server (which stays bound to localhost):
  * bearer API-key check against Supabase `api_keys` (sha256 hex of the key),
    cached 60 s (10 s for misses); revoked keys get 401, and if Supabase is
    unreachable cached keys keep working while unknown keys get 503, not 401.
    $GATEWAY_API_KEY still works as a legacy single key when set.
  * per-request video budget: reads the clip's duration and sets vLLM's
    mm_processor_kwargs so the model sees its training grid (2 fps, 200,704 px
    per frame) instead of the processor default that costs 6x the tokens
    (see results/notes.md); rejects clips over MAX_VIDEO_SECONDS / MAX_VIDEO_MB
  * the media fetch, once, safely (see README "Security"). A caller-supplied
    `video_url` is fetched here and handed to vLLM inline as a base64 `data:`
    URL, so the engine never fetches from the internet and the bytes cross the
    wire once. The fetch itself is SSRF-hardened: http(s) only, every hostname
    resolved and every resolved address checked against loopback / private /
    link-local (169.254.0.0/16, the EC2 metadata endpoint) / multicast /
    reserved, redirects followed by hand (MAX_REDIRECTS hops, re-validated
    every hop), a total FETCH_TIMEOUT_S budget, a streaming byte counter that
    aborts at MAX_VIDEO_MB, and an ALLOWED_VIDEO_MIME content-type/extension
    allowlist. Upstream exception text never reaches the caller: it goes to
    the journal and the client gets a generic 400 plus a reason class
    (dns, blocked-address, too-large, timeout, unsupported-type, http-<status>).
  * `Inference-Id` response header + one JSON line per request in $USAGE_LOG
    (id, tokens, video seconds, TTFT, status), plus one `usage_events` row in
    Supabase per authenticated request, posted from a background queue with
    retries; rows that never land go to usage_failed.jsonl for deploy/replay_usage.py.
    cost_usd uses the `models` prices, re-read every 5 minutes.
  * early 429 above MAX_INFLIGHT instead of queueing
  * strips the leading `<think>` token Marlin emits (non-streaming and streaming)

Env (see deploy/install.sh, which writes /etc/marlin2b-gateway.env):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, MODEL_ID, GATEWAY_API_KEY (legacy,
  optional), UPSTREAM, MAX_INFLIGHT, MAX_VIDEO_SECONDS, MAX_VIDEO_MB,
  FETCH_TIMEOUT_S, MAX_REDIRECTS, ALLOWED_VIDEO_MIME,
  USAGE_LOG, USAGE_FAILED_LOG, MODELS_DOC. With none of them set the gateway
  imports and runs unauthenticated, which is what the tests use.

Run:  uvicorn gateway:app --host 127.0.0.1 --port 8001
Then put TLS in front (Caddyfile) and expose only 443.
"""
import sys
import types

from infrx import usage as _usage_mod
from infrx.auth import keys as _keys_mod
from infrx.gateway.app import create_app
from infrx.gateway.routes import chat as _chat_mod
from infrx.media import video as _video_mod

_app = create_app()
_rt = _app.state.runtime


def _legacy_table(rt):
    """Old module global -> (object, attribute) it now lives on. Reads and writes
    of these names go straight through to the default app, so the existing tests
    (which treat this module as a bag of mutable globals) keep working."""
    s, a, m, u = rt.settings, rt.auth, rt.media, rt.usage
    return {
        # app, injected clients and live counters
        "app": (rt, "app"), "client": (rt, "client"), "sb": (rt, "sb"),
        "inflight": (rt, "inflight"), "clock": (rt, "clock"),
        # configuration
        "UPSTREAM": (s, "upstream"), "LEGACY_KEY": (s, "legacy_key"), "MODEL_ID": (s, "model_id"),
        "MAX_INFLIGHT": (s, "max_inflight"), "MAX_VIDEO_SECONDS": (s, "max_video_seconds"),
        "MAX_VIDEO_MB": (s, "max_video_mb"), "FETCH_TIMEOUT_S": (s, "fetch_timeout_s"),
        "MAX_REDIRECTS": (s, "max_redirects"), "ALLOWED_VIDEO_MIME": (s, "allowed_video_mime"),
        "EXT_MIME": (s, "ext_mime"), "USAGE_LOG": (s, "usage_log"),
        "USAGE_FAILED_LOG": (s, "usage_failed_log"), "SUPABASE_URL": (s, "supabase_url"),
        "SUPABASE_KEY": (s, "supabase_key"), "MODELS_DOC": (s, "models_doc"),
        "FPS": (s, "fps"), "MIN_FRAMES": (s, "min_frames"), "MAX_FRAMES": (s, "max_frames"),
        "PX_PER_FRAME": (s, "px_per_frame"), "KEY_TTL": (s, "key_ttl"), "MISS_TTL": (s, "miss_ttl"),
        "PRICE_TTL": (s, "price_ttl"), "LAST_USED_TTL": (s, "last_used_ttl"),
        "KEY_CACHE_MAX": (s, "key_cache_max"), "MISS_CACHE_MAX": (s, "miss_cache_max"),
        "RETRY_DELAYS": (s, "retry_delays"),
        # auth
        "_keys": (a, "keys"), "_misses": (a, "misses"), "_last_used": (a, "last_used"),
        "authenticate": (a, "authenticate"), "touch": (a, "touch"),
        "hmac": (_keys_mod, "hmac"), "_put": (_keys_mod, "put"),
        # usage
        "_usage_q": (u, "q"), "_worker": (u, "worker"), "_prices": (u, "prices"),
        "ingest": (u, "ingest"), "enqueue": (u, "enqueue"), "spill": (u, "spill"),
        "get_prices": (u, "get_prices"), "cost": (_usage_mod, "cost"),
        # media
        "address_allowed": (_video_mod, "address_allowed"), "resolve_public": (m, "resolve_public"),
        "fetch_client": (m, "fetch_client"), "video_mime": (m, "video_mime"),
        "probe_seconds": (m, "probe_seconds"), "budget_kwargs": (m, "budget_kwargs"),
        "fetch_video": (m, "fetch_video"), "prepare_video": (m, "prepare_video"),
        # routes
        "THINK": (_chat_mod, "THINK"),
    }


_LEGACY = _legacy_table(_rt)


class _LegacyModule(types.ModuleType):
    """Forwards the names in _LEGACY to the default runtime; anything else is an
    ordinary module attribute.

    ponytail: a fixed table, so a new infrx name is not automatically a gateway
    global and `del gateway.<name>` is unsupported. It buys F1 an extraction that
    changes no test and no deployment; once the tests use create_app() directly,
    shrink the table to "app" and delete this class."""

    def __getattr__(self, name):          # only called when the module dict misses
        target = _LEGACY.get(name)
        if target is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return getattr(*target)

    def __setattr__(self, name, value):
        target = _LEGACY.get(name)
        if target is None:
            object.__setattr__(self, name, value)
        else:
            setattr(target[0], target[1], value)


sys.modules[__name__].__class__ = _LegacyModule
