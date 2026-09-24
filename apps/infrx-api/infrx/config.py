"""Gateway configuration: one mutable dataclass, and the only os.environ read.

Field names mirror the original gateway module's globals (UPSTREAM -> upstream,
MAX_VIDEO_MB -> max_video_mb, ...), which the legacy `gateway.py` shim forwarded here
until the cutover retired it, so a config change is one place, not two.

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
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .contracts import money
from .contracts.v2.money_units import ACCOUNTING_REGIMES, CREDIT_REGIME, LEGACY_USD_REGIME, Amount
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
            ".mov": "video/quicktime"}


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
    # F2R item 7: the 08 §5 deployment table, validated by `validate_runtime` before
    # anything mounts. Defaulting to `DEPLOYMENT_DEFAULTS` means nothing is configured.
    deployment: "DeploymentSettings | None" = None

    def __post_init__(self):
        if self.deployment is None:
            self.deployment = DEPLOYMENT_DEFAULTS
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
        deployment=deployment_from_env(e),
    )


def _coerce(name, raw, defaults=None):
    """Coerce by the default's type; error messages name the variable, never its
    value, because DATABASE_URL and friends carry credentials.

    Numbers must be finite and nonnegative: `nan`, `inf` and `-5` are limits that
    would silently disable a bound, and money goes through `money.parse`, which
    rejects exponents and `NaN` outright.
    """
    kind = type(getattr(PILOT_DEFAULTS if defaults is None else defaults, name))
    try:
        if kind is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        value = money.parse(raw.strip()) if kind is Decimal else kind(raw)
    except (ValueError, ArithmeticError, InvalidOperation, TypeError):
        raise ValueError(f"{env_name(name)} is not a valid {kind.__name__}") from None
    if isinstance(value, Amount):
        # A unit-typed amount (contracts v2): `money.parse` already refused NaN, exponents
        # and more than eight places; a negative ceiling is a limit that disables itself.
        if value.is_negative:
            raise ValueError(f"{env_name(name)} must not be negative")
        return value
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
    "max_index_items", "max_index_bytes",
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

    * unset (`INFRX_MODE` absent) - refuses to start (R44, F2.2 carryover 14): the G2
      cutover retired the legacy F1 entry point, and I2's installer always writes a mode.
    * `dev` / `test` - explicit, and no further requirement.
    * `pilot` - requires authentication **and** metering configuration, and refuses the
      shared `GATEWAY_API_KEY` (R51), or a typed `RuntimeMisconfigured` naming the setting
      names and nothing else. A whitespace-only value is not configuration. It also
      refuses while the composition root would serve chat through the legacy F1 route
      (G1R, E3B dr17): only the metered ingress may answer `/v1/chat/completions`.
    * anything else - refuses to start. A typo in a unit file is not a mode.

    In every mode it first refuses a 08 §5 deployment value that cannot serve
    (`validate_deployment`), before the mode is even dispatched on.
    """
    pilot = getattr(settings, "pilot", PILOT_DEFAULTS)
    mode = pilot.infrx_mode
    # Before the mode is even dispatched on: a deployment value that cannot serve is a
    # startup failure in every mode, legacy included. A zero pool or an unrotatable spool
    # segment is not something the legacy path is entitled to either.
    deployment = validate_deployment(getattr(settings, "deployment", DEPLOYMENT_DEFAULTS), mode)
    # The pilot bounds a zero would disable, at startup and in every mode too. `validate_pilot`
    # checks them as well but nothing calls it at runtime, so without this `MAX_INDEX_ITEMS=0`
    # (moved here from the deployment table, item 5) would start and reach the scheduler.
    for name in MUST_BE_POSITIVE:
        if getattr(pilot, name) <= 0:
            raise RuntimeMisconfigured(mode, detail=f"{env_name(name)} must be positive")
    # R69 at startup: a CREDIT-regime deployment with no approved card would admit nothing
    # (every model unpriced) or, worse, be read as free. Any mode, legacy included.
    if deployment.accounting_regime == CREDIT_REGIME \
            and not _configured(pilot.active_rate_card_version):
        raise RuntimeMisconfigured(mode, ("ACTIVE_RATE_CARD_VERSION",))
    # And the card is exact text at startup too (`validate_pilot` is not on this path): a
    # padded name is a card nobody published, in either regime. Whitespace-only is unset
    # (the module rule): the CREDIT check above refuses it as missing, legacy never reads it.
    if _configured(pilot.active_rate_card_version) \
            and pilot.active_rate_card_version != pilot.active_rate_card_version.strip():
        raise RuntimeMisconfigured(
            mode, detail="ACTIVE_RATE_CARD_VERSION must not carry surrounding whitespace")
    if mode == MODE_UNSET:
        raise RuntimeMisconfigured(mode, ("INFRX_MODE",))
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
        # G1R / E3B dr17: the legacy chat route has no durable admission and no hold, so a
        # pilot must not start while it is mounted - or while the metered ingress is not.
        # What serves the path is the composition root's router list; G2's cutover swaps
        # `chat` for `ingress` there. deploy/preflight.py's installer gate (I0) refuses
        # the same composition. Imported here, not at module load: `gateway.app` imports
        # this module.
        from .gateway import app as composition
        from .gateway.routes import chat, ingress
        if ingress not in composition.ROUTERS or chat in composition.ROUTERS:
            # Worded apart from preflight's own gate message, so each refusal stays
            # observable (and killable) on its own.
            raise RuntimeMisconfigured(mode, detail="chat would be served by the legacy "
                                                    "route, not the metered ingress (dr17)")
    return mode


# --- 08 §5 deployment configuration (F2R item 7) ------------------------------------
# Why these are here and not in `contracts/limits.PilotSettings`: that dataclass is
# contract data - numbers both halves enforce, frozen by a contract revision - and it
# lives under `infrx/contracts/`, which is owned by the contract lane. These are
# *deployment* knobs: how large a spool segment is, how big the connection pool is, how
# long a statement may run, how much structure one request body may contain, and the
# secret that signs a console cursor. They are read here, by the same rules, and 08 §5
# lists them in its own table. Folding them into `PilotSettings` later moves the names,
# defaults and checks unchanged; it is an integration request, not a behaviour change.
#
# Every value is provisional (R17): shaped like the contract, numbers this round's
# reading. `⚠️ TO BE VERIFIED` against a real pool and a real spool - D2 owns the pool
# numbers, T2 the segment size.
@dataclass(frozen=True)
class DeploymentSettings:
    # T1/T2: one spool segment. 16 MiB is small enough that a torn tail costs little to
    # quarantine and large enough that rotation is not the hot path.
    trace_spool_segment_bytes: int = 16_777_216
    # contracts v2 (F2P wire-in, item 5): which regime new admissions are written in,
    # `legacy_usd` (today's behaviour) or `credit`. A deployment knob, so an empty value
    # is refused like every other name here; `credit` also needs an approved card
    # (`ACTIVE_RATE_CARD_VERSION`, contract data). D1R's database flags gate the same
    # switch inside the transaction; this one is what the gateway dispatches on.
    accounting_regime: str = LEGACY_USD_REGIME
    # C's keyset cursors are opaque *and* tamper-proof only if they are signed. Read by
    # the console runtime, not by this gateway - which is why an unset secret is not a
    # gateway startup failure: a FastAPI process that serves no console page has nothing
    # to sign, and coupling the inference path to it would refuse to serve inference over
    # a console setting. What is enforced here is the *value*: set, it must be long enough
    # to be a signing key in any mode. Requiring it to be set belongs to the console's own
    # startup (C2) and to the deployment checklist (08 §5).
    console_cursor_secret: str = ""
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    database_pool_connect_timeout_s: float = 5.0
    # A statement with no timeout is a lock held until someone notices.
    database_pool_statement_timeout_ms: int = 15_000
    # G1's structure caps, today module constants in `gateway/routes/validate.py` and
    # `intake.py`. They bound the *shape* of a body before it is parsed, which is what
    # makes a pre-parse structural count possible at all.
    max_messages: int = 64
    max_parts: int = 16
    max_text_codepoints: int = 131_072
    max_url_chars: int = 8_192
    max_number_digits: int = 20
    # One `LargeBodies` per process: at most two bodies over the threshold at a time.
    large_body_limit: int = 2
    large_body_threshold_bytes: int = 1_048_576   # 1 MiB
    # M1-L2: where in `S3_MEDIA_BUCKET` (contract data, 08 §5) the media store keeps its
    # objects, and - for an S3-compatible store (MinIO in tests) - where that store is;
    # unset is AWS S3. Credentials and region are never settings: botocore's own chain
    # reads them (the instance role on the box).
    s3_media_prefix: str = "infrx/"
    s3_endpoint_url: str = ""
    # E4B's served-build check: the commit install.sh deployed (`RELEASE`, "the checkout is
    # exactly a commit") and the runtime image id it built, written by preflight `apply`.
    # Exposed as `infrx_build_info{revision, image} 1`; a pilot refuses to start without
    # them (`pilot.build_info`). Never read from git or docker at runtime.
    infrx_release_sha: str = ""
    infrx_image: str = ""

    def replace(self, **changes):
        return dataclasses.replace(self, **changes)


DEPLOYMENT_DEFAULTS = DeploymentSettings()

# Set, it must be at least this long. Not a password: a signing key.
MIN_CONSOLE_CURSOR_SECRET_CHARS = 16

# A full git commit id, and an image id as `docker image inspect` prints it.
RELEASE_SHA_RE = re.compile(r"[0-9a-f]{40}")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
# One or more path segments, each ending in `/`: never the bucket root, never `//`.
S3_PREFIX_RE = re.compile(r"(?:[A-Za-z0-9._-]+/)+")
# A scheme and an authority only: no path, no query, no `user:password@`.
S3_ENDPOINT_RE = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/?")

# 08 §5: what the *console* runtime requires that this gateway does not. C2 reads the
# secret and signs its cursors with it; the deployment checklist (G2/I2) must write it.
# Recorded here so "the gateway does not require it" is a decision rather than an omission.
CONSOLE_ONLY_SETTINGS = ("CONSOLE_CURSOR_SECRET",)

# Every one of these is a bound or a size a zero or negative value disables rather than
# tightens: a zero pool admits no connection, a zero statement timeout means "no limit"
# in PostgreSQL, a zero message cap refuses every request, a zero segment never rotates.
DEPLOYMENT_MUST_BE_POSITIVE = tuple(
    f.name for f in dataclasses.fields(DeploymentSettings)
    if not isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)
)


def deployment_from_env(env=None):
    """`DeploymentSettings` from the environment, by the 08 §5 name of each field.

    Unlike `pilot_from_env`, an **empty** value is refused rather than read as unset:
    `MAX_MESSAGES=` in a unit file is a mistake, and silently serving the default is how
    an operator believes they configured something they did not. A name that is absent
    altogether takes its default, which is what "unset" means.
    """
    e = os.environ if env is None else env
    values = {}
    for f in dataclasses.fields(DeploymentSettings):
        name = env_name(f.name)
        if name not in e:
            continue
        raw = e[name]
        if raw.strip() == "":
            raise ValueError(f"{name} is set to an empty value: unset it or give it one")
        values[f.name] = (raw.strip() if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)
                          else _coerce(f.name, raw, DEPLOYMENT_DEFAULTS))
    return DeploymentSettings(**values)


def validate_deployment(deployment, mode=MODE_UNSET):
    """Refuse a deployment configuration that cannot serve, before anything mounts.

    Called by `validate_runtime`, so a bad value is a startup failure rather than a
    surprise on the first request that happens to reach the setting. Names only, never
    values: `CONSOLE_CURSOR_SECRET` is a signing key.
    """
    for name in DEPLOYMENT_MUST_BE_POSITIVE:
        if getattr(deployment, name) <= 0:
            raise RuntimeMisconfigured(mode, detail=f"{env_name(name)} must be positive")
    if deployment.accounting_regime not in ACCOUNTING_REGIMES:
        raise RuntimeMisconfigured(
            mode, detail="ACCOUNTING_REGIME must be one of " + ", ".join(ACCOUNTING_REGIMES))
    if deployment.database_pool_min_size > deployment.database_pool_max_size:
        raise RuntimeMisconfigured(
            mode, detail="DATABASE_POOL_MIN_SIZE must not exceed DATABASE_POOL_MAX_SIZE")
    secret = deployment.console_cursor_secret
    if _configured(secret) and len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS:
        # Never the value: this is a signing key, and a startup error is the most widely
        # copied line of text a process ever emits.
        raise RuntimeMisconfigured(
            mode,
            detail=f"CONSOLE_CURSOR_SECRET must be at least "
                   f"{MIN_CONSOLE_CURSOR_SECRET_CHARS} characters")
    if not S3_PREFIX_RE.fullmatch(deployment.s3_media_prefix):
        raise RuntimeMisconfigured(
            mode, detail="S3_MEDIA_PREFIX must be path segments, each ending in /")
    if deployment.s3_endpoint_url and not S3_ENDPOINT_RE.fullmatch(deployment.s3_endpoint_url):
        raise RuntimeMisconfigured(
            mode, detail="S3_ENDPOINT_URL must be http(s)://host[:port], no path or credentials")
    if deployment.infrx_release_sha and not RELEASE_SHA_RE.fullmatch(deployment.infrx_release_sha):
        raise RuntimeMisconfigured(mode, detail="INFRX_RELEASE_SHA must be a 40-hex commit id")
    if deployment.infrx_image and not IMAGE_ID_RE.fullmatch(deployment.infrx_image):
        raise RuntimeMisconfigured(mode, detail="INFRX_IMAGE must be sha256:<64 hex>")
    return deployment


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
    card = pilot.active_rate_card_version
    if card != card.strip():
        raise ValueError("ACTIVE_RATE_CARD_VERSION must not carry surrounding whitespace")
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
