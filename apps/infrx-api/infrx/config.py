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
import math
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .contracts import money
from .contracts.limits import DEFAULTS as PILOT_DEFAULTS
from .contracts.limits import JUDGE_MODES, MODES, PilotSettings, env_name

# gateway.py's directory, i.e. apps/infrx-api: MODELS_DOC used to be resolved
# against it, and that is this package's parent, not the package itself.
_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_MODELS_DOC = os.path.join(_API_DIR, "openrouter", "provider-models.json")
DEFAULT_ALLOWED_VIDEO_MIME = "video/mp4,video/webm,video/quicktime,video/mpeg"
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
    "max_active_jobs", "max_active_jobs_per_org", "max_active_jobs_per_key",
    "max_preparing_jobs", "idempotency_ttl_s", "unknown_usage_reconcile_s",
)


def validate_pilot(pilot, gateway=None):
    """Fail closed at startup: `pilot` mode refuses to run unmetered (no durable
    store) or unauthenticated (no per-key identity source), and live judging
    refuses to run without an explicit budget."""
    if pilot.infrx_mode not in MODES:
        raise ValueError(f"INFRX_MODE must be one of {', '.join(MODES)}")
    if pilot.judge_mode not in JUDGE_MODES:
        raise ValueError(f"JUDGE_MODE must be one of {', '.join(JUDGE_MODES)}")
    if pilot.judge_mode == "live" and pilot.judge_live_budget_usd <= 0:
        raise ValueError("JUDGE_MODE=live requires a positive JUDGE_LIVE_BUDGET_USD")
    for name in MUST_BE_POSITIVE:
        if getattr(pilot, name) <= 0:
            raise ValueError(f"{env_name(name)} must be positive")
    if pilot.infrx_mode == "pilot":
        if not pilot.database_url:
            raise ValueError("INFRX_MODE=pilot requires DATABASE_URL: admission must be metered")
        if gateway is not None and not (gateway.supabase_url and gateway.supabase_key):
            raise ValueError("INFRX_MODE=pilot requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY: "
                             "a shared legacy key is not authenticated per organization")
    return pilot
