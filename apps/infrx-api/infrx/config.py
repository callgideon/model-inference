"""Gateway configuration: one mutable dataclass, and the only os.environ read.

Field names mirror the original gateway module's globals (UPSTREAM -> upstream,
MAX_VIDEO_MB -> max_video_mb, ...) so the legacy shim in gateway.py forwards
assignments straight here, and so a config change is one place, not two.
"""
import os
from dataclasses import dataclass, field

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
