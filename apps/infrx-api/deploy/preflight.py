#!/usr/bin/env python3
"""Fail-closed installation of the gateway environment file, and the startup
prerequisites a pilot install must satisfy before it may replace one.

`O-FAILOPEN` (research/plan/evidence/i/I1-4e052f4.md) is the hazard this module
exists for. The installer used to treat *every* `aws ssm get-parameter` failure as
a warning, truncate `/etc/marlin2b-gateway.env` and restart the gateway anyway; with
neither `GATEWAY_API_KEY` nor `SUPABASE_URL` in the rewritten file the gateway
allowed every request, so one denied or throttled read published an unauthenticated,
unmetered gateway on the pilot hostname. Order of operations here is the fix:

    read every required value -> validate all of them -> stage a private 0600 file
    -> probe the runtime that will read it -> os.replace -> restart

Nothing before `os.replace` touches the installed file, so a failure at any earlier
step leaves the previous file byte-identical and performs no restart. That is the
`DEPLOY-FAILCLOSED` oracle of research/plan/04-verification.md, and the assertion is
on the bytes and the recorded `systemctl` calls, not on an exit code.

Secret handling: a value is read through a captured pipe, is written only into the
0600 staged file, and is never printed, logged or passed as an argument. Diagnostics
name *settings and parameters*, never values - the same rule `config.RuntimeMisconfigured`
follows, and for the same reason: a startup error is the most widely copied line of
text a process ever emits.

    ./preflight.py manifest --mode pilot            # the required keys, names only
    ./preflight.py apply --mode dev --env-file /etc/marlin2b-gateway.env
    ./preflight.py probe --mode pilot --env-file <staged>   # run by `apply`, in the
                                                           # runtime interpreter

This module mutates one file and calls `systemctl`. It performs no AWS write, and
`apply` against the pilot host needs the deployment lock of infra/README.md §1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

# `apps/infrx-api`: the directory the gateway runs from, and the one the runtime
# package is importable from.
API_DIR = pathlib.Path(__file__).resolve().parent.parent

# infra/README.md §5: the mode has no default, because a default is how an unmetered
# pilot happens by accident. The names are `contracts.limits.MODES`; they are repeated
# here rather than imported because `apply` runs under the installer's interpreter,
# which need not be able to import the runtime package at all - that is what `probe`
# is for. `test_manifest.py` pins the two lists against each other.
MODES = ("dev", "test", "pilot")
ALL_MODES = MODES

# M1's finding (research/plan/evidence/m/M1-5ec51e1.md item 6, coordinator STATUS
# 2026-09-22): CPython 3.12.0-3.12.3 answer `is_private` from older special-purpose
# tables, so the media path's address policy depends on the interpreter build.
REQUIRED_PYTHON = (3, 12, 4)
# Where the Dockerfile puts this file inside the runtime image.
IMAGE_PREFLIGHT = "/app/deploy/preflight.py"

# W3 owns the engine image and flags. The adapter cannot read a reasoning-parser
# stream and does not tolerate per-chunk usage, so an installer that finds either
# flag refuses rather than deploying an engine the gateway cannot parse.
FORBIDDEN_ENGINE_FLAGS = ("--reasoning-parser", "continuous_usage_stats")
# W3's record of the served engine, next to serve.sh (models/marlin2b/).
SERVING_VERSION = "serving-version.json"
LOOPBACK_PUBLISH = ("127.0.0.1:", "${BIND:-127.0.0.1}:")
# The worker unit starts `python -m infrx.worker`: the composition root around W3's
# WorkerService (claims, the in-process recover() reaper, drain, loopback readiness). A
# pilot image without it would install a unit that crash-loops.
WORKER_ENTRIES = ("infrx.worker.__main__",)

# est. budgets (infra/README.md §2), checked where each directory lives or will live: the
# usage spill and logs on the root EBS volume (5 GiB + headroom), and the media root on
# the instance-store NVMe (media/staging 60 GiB; a rebuildable cache, W3's proposed R).
DISK_BUDGET = (("/var/lib/infrx", 10 * 2**30), ("/opt/dlami/nvme/processing", 60 * 2**30))

# infra/README.md §5: `/model-inference/price_table_version` was **withdrawn**, not
# reassigned - contracts v1 resolves the price from D1's `price_versions` by model and
# effective time, so a deploy-time pin would be a second, conflicting price authority.
# An env file that carries one is refused whatever mode it is in.
WITHDRAWN_KEYS = ("PRICE_TABLE_VERSION", "PRICE_SOURCE")

# A value with a newline in it writes a second `KEY=VALUE` line into the env file, i.e.
# an SSM parameter (or anything that can write one) chooses `GATEWAY_API_KEY`. Quotes and
# backslashes are refused for the same reason one step further in: systemd's
# `EnvironmentFile` parser gives them meaning (quoted values, escape sequences, line
# continuation) that neither `render` nor `read_env` models, so a value carrying one
# would not arrive at the process as it was read. Operators URL-encode such a value.
# Refused for every key, secret or not: this is the trust boundary of the whole module.
FORBIDDEN_CHARS = ("\n", "\r", "\0", "'", '"', "\\")


# --- the manifest of required keys -------------------------------------------------
@dataclass(frozen=True)
class Key:
    """One env key the gateway needs, and where its value comes from.

    `param` is the leaf under `--param-prefix` when the value lives in SSM; `None`
    means the installer supplies it (a flag, or a documented default). `required_in`
    and `forbidden_in` are modes, so one manifest describes every mode instead of
    three drifting copies.
    """

    env: str
    role: str
    shape: str
    param: str | None = None
    secret: bool = False
    required_in: tuple[str, ...] = ALL_MODES
    forbidden_in: tuple[str, ...] = ()

    def needed(self, mode: str) -> bool:
        return mode in self.required_in

    def banned(self, mode: str) -> bool:
        return mode in self.forbidden_in


MANIFEST: tuple[Key, ...] = (
    Key("INFRX_MODE", "runtime mode (no default)", "mode"),
    Key("MODEL_ID", "served model pin", "model_id"),
    Key("MAX_INFLIGHT", "capacity bound", "positive_int"),
    Key("USAGE_LOG", "usage sink (legacy jsonl spill)", "abs_path"),
    # I2B: the units run this image, and systemd reads the id from this same file, so the
    # image and the configuration it was probed with are replaced by one rename. A pilot
    # never runs on a host interpreter.
    Key("INFRX_IMAGE", "runtime image pin (built image id)", "image_id",
        required_in=("pilot",)),
    # The engine is never public (I2B.b): whatever the flags say, the gateway only ever
    # dials it on loopback, and so does the scheduling index.
    Key("UPSTREAM", "engine address, loopback only", "loopback_url"),
    Key("VALKEY_URL", "scheduling index, loopback only", "loopback_valkey_url",
        required_in=("pilot",)),
    # R61 (2) / W2 request 5: ONE root R shared by the processing cache, the engine
    # adapter's `local_media_root` and vLLM's `--allowed-local-media-path`.
    Key("PROCESSING_CACHE_DIR", "shared media root", "abs_path", required_in=("pilot",)),
    Key("SUPABASE_URL", "identity source", "https_url",
        param="supabase_url", required_in=("pilot",)),
    Key("SUPABASE_SERVICE_ROLE_KEY", "identity source", "opaque",
        param="supabase_service_role_key", secret=True, required_in=("pilot",)),
    # One parameter, two roles: contracts v1 makes PostgreSQL the metering sink *and*
    # the price authority (D1 `price_versions`), which is why no price parameter exists.
    Key("DATABASE_URL", "metering sink and price authority (D1 price_versions)", "pg_dsn",
        param="pg_journal_url", secret=True, required_in=("pilot",)),
    # r1 R51: `Auth.authenticate` answers "allowed, no row" for the shared key, so a
    # request bearing it has no tenant to meter. Optional in dev/test, refused in pilot.
    Key("GATEWAY_API_KEY", "legacy shared key", "opaque",
        param="marlin2b_api_key", secret=True, required_in=(), forbidden_in=("pilot",)),
)


# --- the config schema: every other name the runtime reads (I2B.a) -----------------
# `config.from_env` reads these, and a deployment may set them only as
# `apply --set NAME=VALUE` (not secrets: a secret is a MANIFEST key read from SSM). The
# values are the runtime's to validate - the probe runs `validate_runtime(from_env(...))`
# over the staged bytes - so a mistyped number refuses the install. A mistyped *name* is
# refused here: `MAX_ACTIVE_JOB=4` would otherwise be ignored, and the operator would
# believe the cap was set. Names only; `tests/i` pins this list, the manifest and
# `NOT_SETTABLE` to exactly the names the runtime reads.
TUNABLE = (
    # 08 §5 (contracts.limits.PilotSettings)
    "MAX_REQUEST_BYTES", "INTAKE_TIMEOUT_S", "MAX_MEDIA_BYTES", "MAX_VIDEO_SECONDS",
    "MEDIA_FETCH_CONNECT_TIMEOUT_S", "MEDIA_FETCH_TIMEOUT_S", "MEDIA_FETCH_MAX_REDIRECTS",
    "PROBE_TIMEOUT_S", "PREPARATION_TIMEOUT_S", "TRANSCODE_MIN_TIMEOUT_S",
    "TRANSCODE_DURATION_FACTOR", "PREPARATION_CONCURRENCY", "MAX_PREPARING_JOBS",
    "QUEUE_WAIT_INTERACTIVE_S", "QUEUE_WAIT_ASYNC_S", "GENERATION_TIMEOUT_S",
    "TTFT_TIMEOUT_S", "TPOT_STALL_S", "LEASE_TTL_S", "PREPARATION_LEASE_TTL_S",
    "LEASE_HEARTBEAT_S", "MAX_PREPUBLICATION_RETRIES", "SSE_KEEPALIVE_S", "STREAM_BATCH_MS",
    "JOURNAL_EVENT_MAX_BYTES", "JOURNAL_JOB_RESERVE_BYTES", "JOURNAL_TOTAL_BYTES",
    "JOURNAL_CHUNK_TTL_S", "RESULT_TTL_S", "PROCESSING_CACHE_TTL_S", "IDEMPOTENCY_TTL_S",
    "TRACE_CONTENT_MAX_DAYS", "TRACE_METADATA_MONTHS", "MAX_ACTIVE_JOBS",
    "MAX_ACTIVE_JOBS_PER_ORG", "MAX_ACTIVE_JOBS_PER_KEY", "ENGINE_MAX_NUM_SEQS",
    "WORKER_CONCURRENCY", "MAX_OUTPUT_TOKENS", "MAX_CONTEXT_TOKENS", "TRACE_CAPTURE_BYTES",
    "TRACE_METADATA_RESERVE_BYTES", "TRACE_QUEUE_MAX", "TRACE_SPOOL_DIR",
    "TRACE_SPOOL_MAX_BYTES", "TRACE_SPOOL_MIN_FREE_BYTES", "TRACE_FSYNC_INTERVAL_S",
    "S3_MEDIA_BUCKET", "S3_TRACE_BUCKET", "UNKNOWN_USAGE_RECONCILE_S",
    # moved from DeploymentSettings by the F2P wire-in (item 5, Q2 request 3)
    "MAX_INDEX_ITEMS", "MAX_INDEX_BYTES",
    # F2P wire-in: the CREDIT card gate and the provider dev-wallet ceiling (a Credit amount)
    "ACTIVE_RATE_CARD_VERSION", "PROVIDER_DEV_ALLOCATION_CEILING_CREDIT",
    # 08 §5.1 (config.DeploymentSettings)
    "TRACE_SPOOL_SEGMENT_BYTES",
    "DATABASE_POOL_MIN_SIZE", "DATABASE_POOL_MAX_SIZE", "DATABASE_POOL_CONNECT_TIMEOUT_S",
    "DATABASE_POOL_STATEMENT_TIMEOUT_MS", "MAX_MESSAGES", "MAX_PARTS",
    "MAX_TEXT_CODEPOINTS", "MAX_URL_CHARS", "MAX_NUMBER_DIGITS", "LARGE_BODY_LIMIT",
    "LARGE_BODY_THRESHOLD_BYTES",
    # F2P wire-in: the admission regime, legacy_usd or credit (validate_deployment)
    "ACCOUNTING_REGIME",
    # the F1 names that keep theirs until G/W retire them (08 §5)
    "MAX_VIDEO_MB", "FETCH_TIMEOUT_S", "MAX_REDIRECTS", "ALLOWED_VIDEO_MIME",
    "USAGE_FAILED_LOG", "MODELS_DOC",
)
# Tunables with a shape of their own. The runtime reads 0 here as a valid count (it refuses
# only negatives), and serve.sh hands it to vLLM, which does not start on it: refused
# before anything changes rather than found after the engine restart.
TUNABLE_SHAPES = {"ENGINE_MAX_NUM_SEQS": "positive_int", "WORKER_CONCURRENCY": "positive_int"}
# Read by the runtime, never written by this installer.
NOT_SETTABLE = {
    "CONSOLE_CURSOR_SECRET": "a signing key, and the console's rather than this gateway's "
                             "(08 §5.1); a secret belongs in SSM, not in an argument",
    "CLICKHOUSE_URL": "T's DSN, which carries a credential; SSM when T2 deploys",
    "JUDGE_MODE": "live judging is a separately authorized paid budget",
    "JUDGE_LIVE_BUDGET_USD": "live judging is a separately authorized paid budget",
}


def _matches(pattern: str):
    compiled = re.compile(pattern)
    return lambda value: bool(compiled.fullmatch(value))


def _https_url(value: str) -> bool:
    return bool(re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d{1,5})?/?", value))


def _positive_int(value: str) -> bool:
    # serve.sh's grammar: ASCII, no leading zero (`isdigit` takes '007' and '٣', and
    # `int('²')` raises)
    return bool(re.fullmatch(r"[1-9][0-9]*", value))


def _abs_path(value: str) -> bool:
    return value.startswith("/") and ".." not in value.split("/")


SHAPES = {
    "mode": lambda value: value in MODES,
    # `owner/name`, which is what the gateway resolves against the served map.
    "model_id": _matches(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*"),
    "positive_int": _positive_int,
    "abs_path": _abs_path,
    "https_url": _https_url,
    "pg_dsn": _matches(r"postgres(?:ql)?://[^\s]+"),
    # `docker image inspect --format {{.Id}}`: a content address, never a tag.
    "image_id": _matches(r"sha256:[0-9a-f]{64}"),
    "loopback_url": _matches(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):\d{1,5}/?"),
    "loopback_valkey_url": _matches(r"(?:valkey|redis)://(?:127\.0\.0\.1|localhost):\d{1,5}(?:/\d{1,2})?"),
    # A shared secret shorter than this is a typo, a placeholder or a truncated read.
    "opaque": lambda value: 20 <= len(value) <= 4096,
    # The runtime types a tunable; the probe's `validate_runtime` is what refuses `abc`.
    "tunable": lambda value: len(value) <= 4096,
}


def shape_problem(key: Key, value: str) -> str | None:
    """Why `value` may not be written for `key` - naming the key, never the value."""
    if value == "" or value.strip() == "":
        return f"{key.env}: the value is empty"
    if any(bad in value for bad in FORBIDDEN_CHARS):
        return (f"{key.env}: the value contains a newline, NUL, quote or backslash; "
                f"a newline would write a second variable into the env file and the "
                f"others are special to systemd's EnvironmentFile parser")
    if value != value.strip():
        return f"{key.env}: the value has leading or trailing whitespace"
    if not SHAPES[key.shape](value):
        return f"{key.env}: the value is not a valid {key.shape}"
    return None


# --- reading SSM ------------------------------------------------------------------
# What the three outcomes of a parameter read mean (infra/README.md §5). Only
# `not_found` may ever be tolerated, and only for a key the mode does not require:
# `AccessDenied` and throttling mean "do not deploy", because the value may well exist
# and the install run would otherwise omit an authentication or metering setting.
NOT_FOUND_CODES = ("ParameterNotFound", "ParameterVersionNotFound")
DENIED_CODES = ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
                "UnrecognizedClientException", "InvalidClientTokenId", "ExpiredToken",
                "ExpiredTokenException", "Throttling", "ThrottlingException",
                "RequestLimitExceeded", "TooManyUpdates", "InternalServerError",
                "ServiceUnavailable", "EndpointConnectionError", "ConnectTimeoutError",
                "KMSAccessDeniedException", "InvalidKeyId")

NOT_FOUND, DENIED, UNKNOWN = "not_found", "denied", "unknown"


def classify(stderr: str) -> str:
    """`not_found`, `denied` or `unknown` for a failed `get-parameter`.

    `not_found` is checked first and matched on the exact error codes: an
    `AccessDenied` message that happens to mention a parameter name must not be read
    as "the parameter does not exist". Anything unrecognised is `unknown`, which
    `apply` treats exactly like `denied` - an installer that cannot explain a failure
    has not proved the value is absent.
    """
    for code in NOT_FOUND_CODES:
        if code in stderr:
            return NOT_FOUND
    for code in DENIED_CODES:
        if code in stderr:
            return DENIED
    return UNKNOWN


def read_parameter(name: str, region: str, aws=("aws",)) -> tuple[str | None, str]:
    """`(value, "")` or `(None, kind)`. The value crosses a captured pipe and is
    returned; it is never printed and never becomes an argument of anything."""
    done = subprocess.run([*aws, "ssm", "get-parameter", "--name", name,
                           "--with-decryption", "--region", region,
                           "--query", "Parameter.Value", "--output", "text"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        return None, classify(done.stderr or done.stdout)
    # `--output text` appends exactly one newline; a newline *inside* the value is a
    # shape failure, so only the added one is removed.
    return (done.stdout or "").removesuffix("\n"), ""


# --- collecting and validating the whole file -------------------------------------
@dataclass
class Config:
    mode: str
    env_file: pathlib.Path
    owner: str = "ubuntu"
    region: str = "us-east-1"
    param_prefix: str = "/model-inference"
    runtime_python: str = "/opt/pytorch/bin/python"
    serve_script: pathlib.Path | None = None
    units: tuple[str, ...] = ()
    model_id: str = "nemostation/marlin-2b"
    max_inflight: str = "16"
    # Row M-SCRATCH: the usage spill survives a stop only on the root EBS volume.
    usage_log: str = "/var/lib/infrx/usage/usage.jsonl"
    image: str = ""                   # INFRX_IMAGE; set, the probe runs inside it
    upstream: str = "http://127.0.0.1:8000"
    valkey_url: str = "valkey://127.0.0.1:6379/0"
    media_root: str = "/opt/dlami/nvme/processing"
    settings: tuple[str, ...] = ()    # `--set NAME=VALUE`, schema names only
    disk: tuple[tuple[str, int], ...] = ()
    aws: tuple[str, ...] = ("aws",)
    docker: tuple[str, ...] = ("docker",)
    systemctl: tuple[str, ...] = ("systemctl",)
    calls: list[str] = field(default_factory=list)   # the systemctl calls this run made


def local_values(cfg: Config) -> dict[str, str]:
    """The keys the installer supplies rather than reading from SSM."""
    return {"INFRX_MODE": cfg.mode, "MODEL_ID": cfg.model_id,
            "MAX_INFLIGHT": cfg.max_inflight, "USAGE_LOG": cfg.usage_log,
            "INFRX_IMAGE": cfg.image, "UPSTREAM": cfg.upstream,
            "VALKEY_URL": cfg.valkey_url, "PROCESSING_CACHE_DIR": cfg.media_root}


def collect(cfg: Config) -> tuple[dict[str, str], list[str]]:
    """Every value the env file will contain, and every reason it may not be written.

    Reads **all** required parameters and validates **all** values before returning,
    so one install run reports every problem instead of one per attempt. Nothing here
    touches the installed file.
    """
    values: dict[str, str] = {}
    problems: list[str] = []
    supplied = local_values(cfg)
    for key in MANIFEST:
        if key.banned(cfg.mode):
            # Named, not silently omitted, so an operator reads why the value they set
            # is gone - and the parameter is not read at all, because a secret nobody
            # needs should not cross a pipe.
            print(f"{key.env}: forbidden in {cfg.mode} mode; not read and not written")
            continue
        if key.param is None:
            value = supplied.get(key.env)
            if not value:
                if key.needed(cfg.mode):
                    problems.append(f"{key.env}: no value supplied ({key.role})")
                continue
        else:
            name = f"{cfg.param_prefix}/{key.param}"
            value, kind = read_parameter(name, cfg.region, cfg.aws)
            if value is None:
                if kind == NOT_FOUND and not key.needed(cfg.mode):
                    print(f"{name}: not found; {key.env} is not required in "
                          f"{cfg.mode} mode and is left out")
                    continue
                problems.append(f"{name}: {kind} ({key.env}, {key.role})"
                                + ("" if kind == NOT_FOUND else
                                   " - a denied or unexplained read is not an absent value"))
                continue
        problem = shape_problem(key, value)
        if problem:
            problems.append(problem)
            continue
        values[key.env] = value
    problems += tunables(cfg.settings, values)
    problems += withdrawn(values)
    return values, problems


def tunables(settings, values: dict[str, str]) -> list[str]:
    """`--set NAME=VALUE` pairs, added to `values`: schema names only, each once."""
    problems = []
    for pair in settings:
        name, _, value = pair.partition("=")
        if name not in TUNABLE:
            problems.append(f"--set {name}: not a tunable name (`preflight.py manifest` "
                            f"lists them)" + (f"; {NOT_SETTABLE[name]}"
                                              if name in NOT_SETTABLE else ""))
        elif name in values:
            problems.append(f"--set {name}: given twice")
        else:
            problem = shape_problem(Key(name, "tunable", TUNABLE_SHAPES.get(name, "tunable")),
                                    value)
            if problem:
                problems.append(problem)
            else:
                values[name] = value
    return problems


def withdrawn(values: dict[str, str]) -> list[str]:
    """Keys no env file may carry, whatever the mode."""
    return [f"{key} is withdrawn: D1's price_versions is the only price authority"
            for key in WITHDRAWN_KEYS if key in values]


def render(values: dict[str, str]) -> str:
    """The env file body, in manifest order so a diff of two installs is readable."""
    order = [key.env for key in MANIFEST] + list(TUNABLE)
    return "".join(f"{name}={values[name]}\n" for name in order if name in values)


def read_env(path: pathlib.Path) -> dict[str, str]:
    """Parse a file this module wrote. Not a general dotenv reader: no quoting, no
    `export`, no continuations, because nothing else writes this file - and because
    `FORBIDDEN_CHARS` refuses every character that would need one of them."""
    env = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            env[name] = value
    return env


# --- engine prerequisites ----------------------------------------------------------
def engine_problems(script: pathlib.Path | None, mode: str) -> list[str]:
    """What the vLLM unit would start, checked against what the adapter supports.

    The flags are asserted against the script the unit actually runs. The image digest
    is a **pilot** requirement, satisfied since W3 pinned `models/marlin2b/serve.sh` by
    digest with its `serving-version.json` beside it; an unpinned script still gets the
    refusal text below.
    """
    if script is None:
        return ["no engine script given: pass --serve-script so the flags can be checked"]
    if not script.exists():
        return [f"{script} does not exist: the engine flags cannot be checked"]
    text = script.read_text()
    problems = [f"{script} passes {flag}, which the gateway's adapter does not support"
                for flag in FORBIDDEN_ENGINE_FLAGS if flag in text]
    if mode == "pilot":
        found = re.search(r"IMAGE=\$\{IMAGE:-([^}]*)\}", text)
        image = found.group(1) if found else ""
        if "@sha256:" not in image:
            problems.append(f"{script} does not pin the engine image by digest "
                            f"(IMAGE default is not a @sha256: reference); pilot needs "
                            f"a pinned image - pending on W3")
        # I2B: the engine is private. Every port serve.sh publishes must be on loopback
        # (`-p "127.0.0.1:…"`, or the older `${BIND:-127.0.0.1}` default): anything else
        # is an OpenAI endpoint with no key on the host's public address.
        publishes = re.findall(r'-p\s+"?([^"\s]+)', text)
        if not publishes or not all(spec.startswith(LOOPBACK_PUBLISH) for spec in publishes):
            problems.append(f"{script} publishes the engine beyond 127.0.0.1; the engine "
                            f"is never public")
        # The named placeholder for W3's pin record. Present, it must record the digest
        # serve.sh actually runs, so the two cannot disagree about what "pinned" means.
        pin = script.parent / SERVING_VERSION
        if not pin.exists():
            problems.append(f"PENDING(W3): {pin} does not exist; the pilot engine pin "
                            f"(image digest, flags, EOS ids) is recorded there")
        elif "@sha256:" in image and image.partition("@")[2] not in pin.read_text():
            problems.append(f"{pin} does not record the image digest {script} runs")
    return problems


def disk_problems(budget) -> list[str]:
    """Free bytes below a declared budget refuse a pilot install: a full disk is lost
    usage spill and failed preparations, discovered on the first busy hour."""
    problems = []
    for path, need in budget:
        where = pathlib.Path(path)
        while not where.exists():
            where = where.parent
        free = shutil.disk_usage(where).free
        if free < need:
            problems.append(f"{path}: {free // 2**30} GiB free, the pilot budget is "
                            f"{need // 2**30} GiB (est.)")
    return problems


# --- the runtime probe, run in the interpreter that will serve --------------------
def transport_logger_problems() -> list[str]:
    """`httpx`/`httpcore` must be at WARNING or above, children included.

    At INFO httpx writes `HTTP Request: GET <pinned url>` for every hop - the
    validated IP *and* the caller's signed query - and httpcore writes the same target
    at DEBUG (M1 integration request 5). This is stricter than that request's snippet
    in one way on purpose: the two root names must carry an **explicit** level, not
    `NOTSET`, because a `NOTSET` logger inherits the root logger and a process that
    sets root to DEBUG then leaks every URL. Children may stay `NOTSET`; they inherit
    the explicit level above them.
    """
    problems = []
    for name in ("httpx", "httpcore"):
        level = logging.getLogger(name).level
        if level == logging.NOTSET or level < logging.WARNING:
            problems.append(f"the {name} logger has no level at or above WARNING "
                            f"(it is {logging.getLevelName(level)}): a signed media URL "
                            f"would reach the process log")
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if not name.startswith(("httpx.", "httpcore.")):
            continue
        if isinstance(logger, logging.PlaceHolder):
            continue            # a placeholder holds no level and emits nothing
        if logger.level != logging.NOTSET and logger.level < logging.WARNING:
            problems.append(f"the {name} logger is below WARNING")
        if logging.getLogger(name).getEffectiveLevel() < logging.WARNING:
            problems.append(f"the {name} logger resolves below WARNING")
    return problems


def probe(env_file: pathlib.Path, mode: str) -> dict:
    """Run, in the runtime interpreter, every check that needs the runtime package.

    Returns a JSON-serialisable verdict whose `problems` name settings, modules and
    logger names only. `apply` refuses the install unless `ok` is true.
    """
    version = ".".join(str(part) for part in sys.version_info[:3])
    problems, warnings = [], []
    if sys.version_info[:3] < REQUIRED_PYTHON:
        # A hard refusal in `pilot`, a warning in the explicitly permissive dev/test
        # modes (infra/README.md §5): the interpreter is a host property an operator
        # cannot fix from inside an install run, and dev serves no public traffic.
        (problems if mode == "pilot" else warnings).append(
            f"the runtime interpreter is {version}; "
            f"{'.'.join(str(p) for p in REQUIRED_PYTHON)} or newer is required "
            f"(the media address policy depends on the interpreter's "
            f"special-purpose ranges)")
    if str(API_DIR) not in sys.path:
        # `probe` is normally run by `run_probe`, which sets PYTHONPATH; this makes a
        # hand-run `preflight.py probe` behave the same instead of failing to import.
        sys.path.insert(0, str(API_DIR))
    try:
        from infrx.config import from_env, validate_runtime
        from infrx.gateway import app as composition
        from infrx.gateway.routes import ingress
        # Imported for its effect: the module sets the transport logger levels at
        # import, and the assertion below runs after every import the entry point makes.
        import infrx.media.fetch           # noqa: F401
    except Exception as failure:           # noqa: BLE001 - any import failure is fatal
        problems.append(f"the runtime package does not import: {type(failure).__name__}")
        return {"ok": False, "python": version, "mode": mode, "problems": problems,
                "warnings": warnings}
    staged_env = read_env(env_file)
    if staged_env.get("INFRX_MODE") != mode:
        # The one place the requested mode and the written mode are compared. They can
        # only differ through a bug here, and the cost of not noticing is a file that
        # claims one mode while the checks were run for another.
        problems.append(f"the staged file's INFRX_MODE is not the requested {mode!r}")
    try:
        # The runtime's *own* answer about the exact bytes that would be installed.
        # It duplicates part of the manifest on purpose: if a later track adds a pilot
        # requirement to `validate_runtime`, this refuses the install even though the
        # manifest here has not caught up.
        validated = validate_runtime(from_env(staged_env))
    except Exception as failure:           # noqa: BLE001 - RuntimeMisconfigured and friends
        # `RuntimeMisconfigured` names setting names and no values (r1 R44).
        problems.append(f"the staged configuration does not start: {failure}")
        validated = None
    if validated is not None:
        try:
            # The cutover: what `create_app` composes from these bytes, short of connecting
            # (`adapters_from_env` opens nothing). An adapter it cannot build - today the
            # object store: S3_MEDIA_BUCKET has no adapter yet (M1 limit 2) - is refused
            # here, before the file is replaced and the unit restarted into that refusal.
            from infrx.gateway.pilot import adapters_from_env
            adapters_from_env(from_env(staged_env))
        except Exception as failure:       # noqa: BLE001 - RuntimeMisconfigured and friends
            # a refusal in pilot; dev/test are explicitly permissive (infra/README.md §5)
            composed = problems if mode == "pilot" else warnings
            composed.append(f"the staged configuration does not compose: {failure}")
    if mode == "pilot" and ingress not in composition.ROUTERS:
        problems.append("INFRX_MODE=pilot requires the pilot routers to be composed in "
                        "infrx/gateway/app.py ROUTERS (the G2 cutover); refusing to "
                        "write pilot mode for a runtime that would serve legacy routes")
    if mode == "pilot":
        if os.geteuid() == 0:
            problems.append("the runtime runs as root; the image's USER must be an "
                            "unprivileged uid")
        for entry in WORKER_ENTRIES:
            if not _importable(entry):
                problems.append(f"PENDING(W3): {entry} is not in the runtime; the worker "
                                f"unit starts it")
    problems += transport_logger_problems()
    return {"ok": not problems, "python": version, "mode": mode,
            "validated_mode": validated, "problems": problems, "warnings": warnings}


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ImportError:
        return False


def probe_command(cfg: Config, staged: pathlib.Path) -> list[str]:
    """In the image when there is one: its interpreter, its package and its user are the
    ones that serve. No network, and the staged file arrives on stdin, so no mount and
    no permission on the 0600 file is needed inside."""
    if cfg.image:
        return [*cfg.docker, "run", "--rm", "-i", "--network", "none", cfg.image,
                "python", IMAGE_PREFLIGHT, "probe", "--mode", cfg.mode,
                "--env-file", "/dev/stdin"]
    return [cfg.runtime_python, os.path.abspath(__file__), "probe",
            "--mode", cfg.mode, "--env-file", str(staged)]


def run_probe(cfg: Config, staged: pathlib.Path) -> dict:
    """Run `probe` in `cfg.runtime_python` against the staged file.

    The staged path is an argument; the values inside it are not. The runtime
    interpreter is the one under test, so a check of its version, its imports and its
    logging cannot be answered by the installer's interpreter.
    """
    path = str(API_DIR)
    inherited = os.environ.get("PYTHONPATH")
    with open(staged, "rb") as body:
        done = subprocess.run(probe_command(cfg, staged), stdin=body,
                              capture_output=True, cwd=path,
                              env={**os.environ,
                                   "PYTHONPATH": f"{path}:{inherited}" if inherited else path})
    done.stdout, done.stderr = done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace")
    try:
        return json.loads(done.stdout)
    except (json.JSONDecodeError, TypeError):
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        return {"ok": False, "python": None, "mode": cfg.mode, "warnings": [],
                "problems": [f"the runtime probe did not answer "
                             f"(exit {done.returncode}): {tail[-1] if tail else 'no output'}"]}


# --- staging and committing --------------------------------------------------------
def staged_name(target: pathlib.Path) -> pathlib.Path:
    return target.parent / f".{target.name}.{os.getpid()}.tmp"


def clear_stale(target: pathlib.Path) -> list[pathlib.Path]:
    """Remove staged files a crashed run left behind.

    They are mode 0600 but they hold secrets, and /etc is forever. A concurrent
    installer is excluded by the deployment lock (infra/README.md §1), so any file
    matching the pattern is abandoned.
    """
    removed = []
    for stale in sorted(target.parent.glob(f".{target.name}.*.tmp")):
        stale.unlink()
        removed.append(stale)
    return removed


def stage(values: dict[str, str], target: pathlib.Path, owner: str) -> pathlib.Path:
    """Write the validated body to a private file beside the target, and return it.

    0600 and the final owner are set **before** the rename, because the rename
    preserves them: there is no window in which the installed file is readable by
    anyone else. `O_EXCL` means a leftover name is an error, not a file to append to.
    """
    staged = staged_name(target)
    handle = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if owner:
            shutil.chown(staged, user=owner)
        os.write(handle, render(values).encode())
        os.fsync(handle)
    except BaseException:
        os.close(handle)
        staged.unlink(missing_ok=True)
        raise
    os.close(handle)
    return staged


def commit(staged: pathlib.Path, target: pathlib.Path) -> None:
    """Rename, then fsync the **directory**: the file's own bytes are already durable
    (`stage` fsyncs them), but the rename itself lives in the directory, and a power
    loss between the two leaves the target pointing at nothing."""
    os.replace(staged, target)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def systemctl(cfg: Config, *args: str) -> int:
    cfg.calls.append(" ".join(args))
    return subprocess.run([*cfg.systemctl, *args]).returncode


# --- apply ------------------------------------------------------------------------
REFUSED, RESTART_FAILED = 2, 3


def report(problems: list[str]) -> None:
    print("refusing to install: the previous env file and the running services are "
          "untouched", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def apply(cfg: Config) -> int:
    """Read, validate, stage, probe, replace, restart - and stop at the first step
    that fails, before the installed file or any service is touched."""
    if cfg.mode not in MODES:
        report([f"INFRX_MODE={cfg.mode!r} is not one of {', '.join(MODES)}; there is "
                f"no default mode"])
        return REFUSED
    values, problems = collect(cfg)
    problems += engine_problems(cfg.serve_script, cfg.mode)
    if cfg.mode == "pilot":
        problems += disk_problems(cfg.disk)
    if problems:
        report(problems)
        return REFUSED
    print(f"mode={cfg.mode} keys=" + ",".join(sorted(values)))
    clear_stale(cfg.env_file)
    staged = stage(values, cfg.env_file, cfg.owner)
    try:
        verdict = run_probe(cfg, staged)
        for warning in verdict.get("warnings", ()):
            print(f"warning: {warning}", file=sys.stderr)
        if not verdict["ok"]:
            report(verdict["problems"])
            return REFUSED
        print(f"runtime ok: python={verdict['python']} mode={verdict.get('validated_mode')}")
        commit(staged, cfg.env_file)
    finally:
        staged.unlink(missing_ok=True)
    print(f"installed {cfg.env_file}")
    failed = []
    if cfg.units:
        if systemctl(cfg, "daemon-reload") != 0:
            failed.append("daemon-reload")
        elif systemctl(cfg, "restart", *cfg.units) != 0:
            failed.append("restart " + " ".join(cfg.units))
    if failed:
        # The file is validated, so it is not rolled back: a service that will not
        # start is an alert, and the rollback path is infra/README.md §8 (compatible
        # runtime or maintenance 503, never unmetered serving).
        print(f"the new env file is installed but `systemctl {failed[0]}` failed; the "
              f"units are not serving the new configuration", file=sys.stderr)
        return RESTART_FAILED
    return 0


# --- CLI --------------------------------------------------------------------------
def build(args) -> Config:
    return Config(mode=args.mode, env_file=pathlib.Path(args.env_file), owner=args.owner,
                  region=args.region, param_prefix=args.param_prefix,
                  runtime_python=args.runtime_python,
                  serve_script=pathlib.Path(args.serve_script) if args.serve_script else None,
                  units=tuple(args.restart), model_id=args.model_id,
                  max_inflight=args.max_inflight, usage_log=args.usage_log,
                  image=args.image, upstream=args.upstream, valkey_url=args.valkey_url,
                  media_root=args.media_root, settings=tuple(args.set),
                  disk=DISK_BUDGET)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    apply_parser = sub.add_parser("apply", help="install the env file, fail closed")
    apply_parser.add_argument("--mode", required=True, help=f"one of {', '.join(MODES)}")
    apply_parser.add_argument("--env-file", required=True)
    apply_parser.add_argument("--owner", default="ubuntu",
                              help="owner of the installed file; empty to leave it")
    apply_parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    apply_parser.add_argument("--param-prefix", default="/model-inference")
    apply_parser.add_argument("--runtime-python", default="/opt/pytorch/bin/python")
    apply_parser.add_argument("--serve-script", default=None)
    apply_parser.add_argument("--restart", action="append", default=[],
                              help="unit to restart after a successful install")
    apply_parser.add_argument("--model-id", default="nemostation/marlin-2b")
    apply_parser.add_argument("--max-inflight", default="16")
    apply_parser.add_argument("--usage-log", default="/var/lib/infrx/usage/usage.jsonl")
    apply_parser.add_argument("--image", default="",
                              help="runtime image id (sha256:...); the probe runs in it")
    apply_parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    apply_parser.add_argument("--valkey-url", default="valkey://127.0.0.1:6379/0")
    apply_parser.add_argument("--media-root", default="/opt/dlami/nvme/processing")
    apply_parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                              help="a tunable from the schema (`manifest` lists them)")

    probe_parser = sub.add_parser("probe", help="runtime checks (run by apply)")
    probe_parser.add_argument("--mode", required=True)
    probe_parser.add_argument("--env-file", required=True)

    manifest_parser = sub.add_parser("manifest", help="the required keys, names only")
    manifest_parser.add_argument("--mode", default="pilot")

    args = parser.parse_args(argv)
    if args.command == "probe":
        verdict = probe(pathlib.Path(args.env_file), args.mode)
        print(json.dumps(verdict))
        return 0 if verdict["ok"] else REFUSED
    if args.command == "manifest":
        for key in MANIFEST:
            where = f"ssm:{key.param}" if key.param else "installer"
            state = ("forbidden" if key.banned(args.mode)
                     else "required" if key.needed(args.mode) else "optional")
            print(f"{key.env:28s} {state:9s} {where:32s} {key.shape:13s} {key.role}")
        print("tunable (--set NAME=VALUE): " + " ".join(TUNABLE))
        print("never written: " + " ".join(sorted(NOT_SETTABLE)))
        return 0
    return apply(build(args))


if __name__ == "__main__":
    raise SystemExit(main())
