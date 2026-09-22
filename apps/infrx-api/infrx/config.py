"""Gateway configuration: one mutable dataclass, and the only os.environ read.

Field names mirror the original gateway module's globals (UPSTREAM -> upstream,
MAX_VIDEO_MB -> max_video_mb, ...) so the legacy shim in gateway.py forwards
assignments straight here, and so a config change is one place, not two.

`Settings`/`from_env` are the F1 gateway's own configuration and keep their exact
defaults. The pilot settings of contracts v1 (08 §5) are a separate object:
`contracts.limits.PilotSettings` holds the frozen names and defaults as pure
data, and `pilot_from_env` below is the only place they are read from the
environment. Nothing in the F1 path reads them, so adding them changes no
existing behaviour.
"""
import dataclasses
import logging
import math
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .contracts import money
from .contracts.limits import DEFAULTS as PILOT_DEFAULTS
from .contracts.limits import JUDGE_MODES, MODE_UNSET, MODES, PilotSettings, env_name

# gateway.py's directory, i.e. apps/infrx-api: MODELS_DOC used to be resolved
# against it, and that is this package's parent, not the package itself.
_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_MODELS_DOC = os.path.join(_API_DIR, "openrouter", "provider-models.json")
# No `video/mpeg` (F2R, coordinator relay): the pilot's pinned profile does not serve it,
# so ingress refuses it up front rather than fetching bytes preparation must reject.
DEFAULT_ALLOWED_VIDEO_MIME = "video/mp4,video/webm,video/quicktime"
EXT_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
            ".mov": "video/quicktime", ".mpeg": "video/mpeg", ".mpg": "video/mpeg"}


def _mimes(raw):
    return {m.strip().lower() for m in raw.split(",") if m.strip()}


@dataclass
class Settings:
    """Mutable on purpose: tests and the legacy shim assign single fields, and
    every reader re-reads them per request."""
    upstream: str = "http://127.0.0.1:8000"
    legacy_key: str = ""
    model_id: str = "nemostation/marlin-2b"
    max_inflight: int = 16
    max_video_seconds: float = 120.0
    max_video_mb: float = 64.0
    fetch_timeout_s: float = 30.0
    max_redirects: int = 3
    allowed_video_mime: set = field(default_factory=lambda: _mimes(DEFAULT_ALLOWED_VIDEO_MIME))
    ext_mime: dict = field(default_factory=lambda: dict(EXT_MIME))
    usage_log: str = "/opt/dlami/nvme/logs/usage.jsonl"
    usage_failed_log: str = None        # None (not "") is "unset": derived from usage_log below
    supabase_url: str = ""
    supabase_key: str = ""
    models_doc: str = DEFAULT_MODELS_DOC
    fps: float = 2.0
    min_frames: int = 4
    max_frames: int = 240
    px_per_frame: int = 200704
    key_ttl: int = 60
    miss_ttl: int = 10
    price_ttl: int = 300
    last_used_ttl: int = 60
    # negatives are attacker-suppliable: own, smaller cap
    key_cache_max: int = 10_000
    miss_cache_max: int = 1_000
    retry_delays: tuple = (1, 3, 9, 0)  # usage_events insert backoff; 0 = give up and spill to disk
    # r1 R44: the contracts-v1 settings travel with the gateway's own, so
    # `create_app(settings=...)` can be handed a whole runtime configuration and
    # `validate_runtime` needs no second argument and no environment read of its own.
    # Defaulting to `PILOT_DEFAULTS` means `INFRX_MODE` is unset, i.e. legacy.
    pilot: PilotSettings = PILOT_DEFAULTS

    def __post_init__(self):
        # only unset derives; USAGE_FAILED_LOG="" stayed "" in the old gateway
        if self.usage_failed_log is None:
            self.usage_failed_log = os.path.join(os.path.dirname(self.usage_log), "usage_failed.jsonl")


def from_env(env=None):
    """Settings from the process environment (see the gateway module docstring).
    The only environment read in the package; everything else takes a Settings."""
    e = os.environ if env is None else env
    return Settings(
        upstream=e.get("UPSTREAM", "http://127.0.0.1:8000"),
        legacy_key=e.get("GATEWAY_API_KEY", ""),
        model_id=e.get("MODEL_ID", "nemostation/marlin-2b"),
        max_inflight=int(e.get("MAX_INFLIGHT", "16")),
        max_video_seconds=float(e.get("MAX_VIDEO_SECONDS", "120")),
        max_video_mb=float(e.get("MAX_VIDEO_MB", "64")),
        fetch_timeout_s=float(e.get("FETCH_TIMEOUT_S", "30")),
        max_redirects=int(e.get("MAX_REDIRECTS", "3")),
        allowed_video_mime=_mimes(e.get("ALLOWED_VIDEO_MIME", DEFAULT_ALLOWED_VIDEO_MIME)),
        usage_log=e.get("USAGE_LOG", "/opt/dlami/nvme/logs/usage.jsonl"),
        usage_failed_log=e.get("USAGE_FAILED_LOG"),
        supabase_url=e.get("SUPABASE_URL", "").rstrip("/"),
        supabase_key=e.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        models_doc=e.get("MODELS_DOC", DEFAULT_MODELS_DOC),
        pilot=pilot_from_env(e),
    )


def _coerce(name, raw):
    """Coerce by the default's type; error messages name the variable, never its
    value, because DATABASE_URL and friends carry credentials.

    Numbers must be finite and nonnegative: `nan`, `inf` and `-5` are limits that
    would silently disable a bound, and money goes through `money.parse`, which
    rejects exponents and `NaN` outright.
    """
    kind = type(getattr(PILOT_DEFAULTS, name))
    try:
        if kind is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        value = money.parse(raw.strip()) if kind is Decimal else kind(raw)
    except (ValueError, ArithmeticError, InvalidOperation):
        raise ValueError(f"{env_name(name)} is not a valid {kind.__name__}") from None
    if kind is float and not math.isfinite(value):
        raise ValueError(f"{env_name(name)} must be a finite {kind.__name__}")
    if kind in (int, float, Decimal) and value < 0:
        raise ValueError(f"{env_name(name)} must not be negative")
    return value


def pilot_from_env(env=None):
    """PilotSettings from the environment: every 08 §5 name, defaults frozen in
    contracts.limits. An empty value means unset, i.e. the default."""
    e = os.environ if env is None else env
    values = {}
    for f in dataclasses.fields(PilotSettings):
        raw = e.get(env_name(f.name))
        if raw is None or raw == "":
            continue
        values[f.name] = _coerce(f.name, raw)
    return PilotSettings(**values)


# Bounds a zero would disable rather than tighten (a zero journal budget admits
# nothing; a zero lease TTL fences every worker instantly).
MUST_BE_POSITIVE = (
    "max_request_bytes", "max_media_bytes", "journal_event_max_bytes",
    "journal_job_reserve_bytes", "journal_total_bytes", "lease_ttl_s", "lease_heartbeat_s",
    "preparation_lease_ttl_s",
    "max_active_jobs", "max_active_jobs_per_org", "max_active_jobs_per_key",
    "max_preparing_jobs", "idempotency_ttl_s", "unknown_usage_reconcile_s",
)


# Filesystem roots: unset (empty) disables the feature; set, the value must be an absolute
# path with no surrounding whitespace - a relative root would follow the process's working
# directory, and `" /var/x"` is a directory nobody meant.
PATH_SETTINGS = ("trace_spool_dir", "processing_cache_dir")


class RuntimeMisconfigured(ValueError):
    """r1 R44: a typed startup error. It names the missing *setting names* and never a
    value, because `DATABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` carry credentials and a
    startup error is the most widely copied line of text a process ever emits.

    A `ValueError` subclass so every existing `pytest.raises(ValueError)` around
    `validate_pilot` still holds; callers that want the distinction catch this.
    """

    def __init__(self, mode: str, missing=(), detail: str = "", forbidden=()) -> None:
        self.mode = mode
        self.missing = tuple(missing)
        self.forbidden = tuple(forbidden)
        parts = []
        if self.missing:
            parts.append("requires " + ", ".join(self.missing))
        if self.forbidden:
            parts.append("must not set " + ", ".join(self.forbidden))
        message = f"INFRX_MODE={mode!r}: " + (detail or "; ".join(parts))
        super().__init__(message)


# r1 R44/R51: what `pilot` needs before it may serve one request. Authentication is a
# per-organization identity source; metering is a durable store, because an unmetered
# pilot is a free-for-all.
PILOT_AUTH_SETTINGS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
PILOT_METERING_SETTINGS = ("DATABASE_URL",)
# r1 R51: and what `pilot` must **not** have. `Auth.authenticate` answers `(None, None)`
# - allowed, with **no row** - for a request bearing the shared `GATEWAY_API_KEY`, so such
# a request has no organization and no key id and therefore nothing to meter, entitle or
# suspend. The comment here used to claim a shared key "fails"; it does not, it succeeds
# anonymously, which is worse. Requiring `SUPABASE_URL` closes the other anonymous path:
# with no Supabase configured and no legacy key, `authenticate` also answers `(None, None)`.
PILOT_FORBIDDEN_SETTINGS = ("GATEWAY_API_KEY",)


def _configured(value: object) -> bool:
    """A setting counts as configured only with non-whitespace content.

    `SUPABASE_URL=" "` passed a plain truthiness test and then built a client pointed at
    `" /rest/v1"`, so the pilot started "authenticated" against nothing at all. Unit files
    and `.env` files make a stray space easy to write and impossible to see.
    """
    return bool(value) and bool(str(value).strip())


def runtime_mode(settings) -> str:
    """The mode a `Settings` is running in: `""` when `INFRX_MODE` is unset (legacy)."""
    return getattr(settings, "pilot", PILOT_DEFAULTS).infrx_mode


def validate_runtime(settings):
    """r1 R44: the one hook `create_app` calls. Returns the mode it validated.

    * unset (`INFRX_MODE` absent) - **legacy F1 behaviour, exactly as before**, logged
      once as `legacy`. F1 preserved behaviour by rule, so the legacy `gateway:app`
      entry point must keep working untouched; G1 replaces this branch with a refusal at
      cutover, in the same change in which I2's installer writes `INFRX_MODE=pilot`.
    * `dev` / `test` - explicit, and no further requirement.
    * `pilot` - requires authentication **and** metering configuration, and refuses the
      shared `GATEWAY_API_KEY` (R51), or a typed `RuntimeMisconfigured` naming the setting
      names and nothing else. A whitespace-only value is not configuration.
    * anything else - refuses to start. A typo in a unit file is not a mode.
    """
    pilot = getattr(settings, "pilot", PILOT_DEFAULTS)
    mode = pilot.infrx_mode
    if mode == MODE_UNSET:
        logging.getLogger("infrx").info(
            "INFRX_MODE is unset: serving legacy F1 behaviour (mode=legacy)")
        return "legacy"
    if mode not in MODES:
        raise RuntimeMisconfigured(mode, detail="must be one of " + ", ".join(MODES))
    if mode == "pilot":
        present = {"DATABASE_URL": pilot.database_url,
                   "SUPABASE_URL": settings.supabase_url,
                   "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_key}
        missing = [name for name in PILOT_METERING_SETTINGS + PILOT_AUTH_SETTINGS
                   if not _configured(present[name])]
        # r1 R51: a shared key is not tenant authentication, and it bypasses metering.
        forbidden = [name for name, value in (("GATEWAY_API_KEY", settings.legacy_key),)
                     if name in PILOT_FORBIDDEN_SETTINGS and _configured(value)]
        if missing or forbidden:
            raise RuntimeMisconfigured(mode, missing, forbidden=forbidden)
    return mode


def validate_pilot(pilot, gateway=None):
    """Fail closed at startup: `pilot` mode refuses to run unmetered (no durable
    store) or unauthenticated (no per-key identity source), and live judging
    refuses to run without an explicit budget.

    The limit-by-limit half of R44's check. `validate_runtime` is what `create_app`
    calls; this one validates a `PilotSettings` on its own and is what a track uses
    when it has no gateway `Settings` to hand.
    """
    if pilot.infrx_mode not in MODES and pilot.infrx_mode != MODE_UNSET:
        raise ValueError(f"INFRX_MODE must be one of {', '.join(MODES)}")
    if pilot.judge_mode not in JUDGE_MODES:
        raise ValueError(f"JUDGE_MODE must be one of {', '.join(JUDGE_MODES)}")
    if pilot.judge_mode == "live" and pilot.judge_live_budget_usd <= 0:
        raise ValueError("JUDGE_MODE=live requires a positive JUDGE_LIVE_BUDGET_USD")
    for name in MUST_BE_POSITIVE:
        if getattr(pilot, name) <= 0:
            raise ValueError(f"{env_name(name)} must be positive")
    for name in PATH_SETTINGS:
        path = getattr(pilot, name)
        if path and (path != path.strip() or not os.path.isabs(path)):
            raise ValueError(f"{env_name(name)} must be unset or an absolute path")
    if pilot.infrx_mode == "pilot":
        if not _configured(pilot.database_url):
            raise ValueError("INFRX_MODE=pilot requires DATABASE_URL: admission must be metered")
        if gateway is not None and not (_configured(gateway.supabase_url)
                                        and _configured(gateway.supabase_key)):
            raise ValueError("INFRX_MODE=pilot requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY: "
                             "a shared legacy key is not authenticated per organization")
        if gateway is not None and _configured(gateway.legacy_key):
            # r1 R51: `Auth.authenticate` answers "allowed, no row" for the shared key, so
            # a request bearing it has no tenant to meter. Not a fallback - a hole.
            raise ValueError("INFRX_MODE=pilot must not set GATEWAY_API_KEY")
    return pilot
