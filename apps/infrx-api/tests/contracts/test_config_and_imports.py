#!/usr/bin/env python3
"""F-CONTRACT / F-BASE: configuration names, frozen defaults, and the import
boundary that keeps a track's extra out of the request path.

    uv run --frozen pytest -q tests/contracts/test_config_and_imports.py
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from decimal import Decimal

import pytest
from infrx import config
from infrx.contracts import limits, tasklocal
from infrx.contracts.v2.money_units import Credit

# 08 §5, name by name. A rename or a changed default is a contract revision, so it
# must fail here first.
EXPECTED = {
    # r1 R44: no default. The empty string is "unset", which `validate_runtime` maps to
    # the legacy F1 behaviour; `dev` was a default that let a production host run in it.
    "INFRX_MODE": "", "DATABASE_URL": "",
    "MAX_REQUEST_BYTES": 100663296, "INTAKE_TIMEOUT_S": 30.0,
    "MAX_MEDIA_BYTES": 67108864, "MAX_VIDEO_SECONDS": 120.0,
    # r1 R2: the pilot fetch limits are MEDIA_FETCH_*; F1's FETCH_TIMEOUT_S (30)
    # keeps its own name, default and reader in config.Settings.
    "MEDIA_FETCH_CONNECT_TIMEOUT_S": 3.0, "MEDIA_FETCH_TIMEOUT_S": 20.0,
    "MEDIA_FETCH_MAX_REDIRECTS": 3, "PROBE_TIMEOUT_S": 10.0,
    "PREPARATION_TIMEOUT_S": 120.0, "TRANSCODE_MIN_TIMEOUT_S": 15.0,
    "TRANSCODE_DURATION_FACTOR": 0.5, "PREPARATION_CONCURRENCY": 2,
    # r1 R1: PREPARATION_CONCURRENCY is the host pool; admission reserves against
    # MAX_PREPARING_JOBS.
    "MAX_PREPARING_JOBS": 8,
    "QUEUE_WAIT_INTERACTIVE_S": 10.0, "QUEUE_WAIT_ASYNC_S": 600.0,
    "GENERATION_TIMEOUT_S": 300.0, "TTFT_TIMEOUT_S": 60.0, "TPOT_STALL_S": 20.0,
    "LEASE_TTL_S": 120.0, "LEASE_HEARTBEAT_S": 40.0, "MAX_PREPUBLICATION_RETRIES": 2,
    # r1 R52: preparation leases are shorter than inference ones, so a lost
    # preparation worker is reaped while its phase budget still has room in it.
    "PREPARATION_LEASE_TTL_S": 30.0,
    "SSE_KEEPALIVE_S": 10.0, "STREAM_BATCH_MS": 50,
    "JOURNAL_EVENT_MAX_BYTES": 1048576, "JOURNAL_JOB_RESERVE_BYTES": 16777216,
    "JOURNAL_TOTAL_BYTES": 1073741824, "JOURNAL_CHUNK_TTL_S": 3600.0,
    "RESULT_TTL_S": 86400.0, "PROCESSING_CACHE_TTL_S": 604800.0, "PROCESSING_CACHE_DIR": "",
    "IDEMPOTENCY_TTL_S": 86400.0, "TRACE_CONTENT_MAX_DAYS": 90,
    "TRACE_METADATA_MONTHS": 13, "MAX_ACTIVE_JOBS": 64,
    "MAX_ACTIVE_JOBS_PER_ORG": 16, "MAX_ACTIVE_JOBS_PER_KEY": 8,
    "ENGINE_MAX_NUM_SEQS": 8, "WORKER_CONCURRENCY": 10,
    "MAX_OUTPUT_TOKENS": 2048, "MAX_CONTEXT_TOKENS": 32768,
    "TRACE_CAPTURE_BYTES": 268435456, "TRACE_METADATA_RESERVE_BYTES": 8388608,
    "TRACE_QUEUE_MAX": 10000, "TRACE_SPOOL_DIR": "",
    "TRACE_SPOOL_MAX_BYTES": 10737418240, "TRACE_SPOOL_MIN_FREE_BYTES": 2147483648,
    "TRACE_FSYNC_INTERVAL_S": 2.0, "JUDGE_MODE": "dry_run",
    "JUDGE_LIVE_BUDGET_USD": Decimal("0"), "VALKEY_URL": "", "CLICKHOUSE_URL": "",
    "S3_MEDIA_BUCKET": "", "S3_TRACE_BUCKET": "",
    # r1 R14: the 24h window 02 requires before an unknown-usage hold is freed
    "UNKNOWN_USAGE_RECONCILE_S": 86400.0,
    # Q2 request 3: the scheduler index caps, moved here from the deployment table
    "MAX_INDEX_ITEMS": 500, "MAX_INDEX_BYTES": 268435456,
    # contracts v2 (F2P wire-in item 5): contract data, empty allowed
    "ACTIVE_RATE_CARD_VERSION": "",
    "PROVIDER_DEV_ALLOCATION_CEILING_CREDIT": Credit("0.00000000"),
}


def test_the_f1_fetch_names_are_not_pilot_names():
    """r1 R2: no pilot setting reuses an F1 gateway variable, so the legacy
    30 s FETCH_TIMEOUT_S cannot be changed by a contracts-v1 name (or vice versa)."""
    legacy = {"FETCH_TIMEOUT_S", "MAX_VIDEO_MB", "MAX_REDIRECTS", "MAX_INFLIGHT",
              "UPSTREAM", "GATEWAY_API_KEY", "USAGE_LOG", "ALLOWED_VIDEO_MIME"}
    assert legacy.isdisjoint(set(EXPECTED))
    assert config.from_env({"FETCH_TIMEOUT_S": "30"}).fetch_timeout_s == 30.0
    assert config.pilot_from_env({"MEDIA_FETCH_TIMEOUT_S": "9"}).media_fetch_timeout_s == 9.0


def test_every_configuration_name_and_default_is_frozen():
    actual = {limits.env_name(f.name): getattr(limits.DEFAULTS, f.name)
              for f in dataclasses.fields(limits.PilotSettings)}
    assert actual == EXPECTED


def test_settings_are_frozen_and_replaceable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.DEFAULTS.max_active_jobs = 1
    assert limits.DEFAULTS.replace(max_active_jobs=2).max_active_jobs == 2
    assert limits.DEFAULTS.max_active_jobs == 64


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_name_is_read_from_the_environment(name):
    raw = {"INFRX_MODE": "test", "JUDGE_MODE": "live", "DATABASE_URL": "postgresql:///x",
           "TRACE_SPOOL_DIR": "/tmp/spool",
           "PROCESSING_CACHE_DIR": "/tmp/processing"}.get(name, "7")
    pilot = config.pilot_from_env({name: raw})
    field_name = limits.fields_by_env()[name].name
    kind = type(getattr(limits.DEFAULTS, field_name))
    assert getattr(pilot, field_name) == kind(raw)


def test_the_default_video_allow_list_has_no_mpeg():
    """F2R: `video/mpeg` is not a pilot input; the default allow-list M's fetcher and the
    F1 gateway both read leaves it out, so ingress refuses it."""
    assert config.DEFAULT_ALLOWED_VIDEO_MIME.split(",") == [
        "video/mp4", "video/webm", "video/quicktime"]
    assert "video/mpeg" not in config.Settings().allowed_video_mime
    # nor does an extension guess name it
    assert "video/mpeg" not in config.EXT_MIME.values()


@pytest.mark.parametrize("name", ["TRACE_SPOOL_DIR", "PROCESSING_CACHE_DIR"])
def test_a_filesystem_root_is_unset_or_absolute(name):
    """F2R: an empty root disables the feature; a set one is an absolute path."""
    field = limits.fields_by_env()[name].name
    assert getattr(limits.DEFAULTS, field) == ""
    config.validate_pilot(config.pilot_from_env({name: "/var/lib/infrx/x"}))
    config.validate_pilot(config.pilot_from_env({name: ""}))
    for bad in ("relative/dir", " /var/lib/infrx/x", "/var/lib/infrx/x\n"):
        with pytest.raises(ValueError, match=name):
            config.validate_pilot(limits.DEFAULTS.replace(**{field: bad}))


def test_pilot_mode_fails_closed():
    """`pilot` refuses to start unmetered or without per-organization identity."""
    unmetered = config.pilot_from_env({"INFRX_MODE": "pilot"})
    with pytest.raises(ValueError, match="DATABASE_URL"):
        config.validate_pilot(unmetered)
    metered = config.pilot_from_env({"INFRX_MODE": "pilot", "DATABASE_URL": "postgresql:///x"})
    gateway = config.from_env({})
    with pytest.raises(ValueError, match="SUPABASE"):
        config.validate_pilot(metered, gateway)
    authenticated = config.from_env({"SUPABASE_URL": "https://example.supabase.co",
                                     "SUPABASE_SERVICE_ROLE_KEY": "not-a-real-key"})
    assert config.validate_pilot(metered, authenticated) is metered


def test_live_judging_needs_an_explicit_budget():
    with pytest.raises(ValueError, match="JUDGE_LIVE_BUDGET_USD"):
        config.validate_pilot(config.pilot_from_env({"JUDGE_MODE": "live"}))
    assert config.validate_pilot(config.pilot_from_env(
        {"JUDGE_MODE": "live", "JUDGE_LIVE_BUDGET_USD": "5"})).judge_live_budget_usd == 5


def test_invalid_values_are_rejected_without_echoing_them():
    with pytest.raises(ValueError) as caught:
        config.pilot_from_env({"DATABASE_URL": "postgresql://u:hunter2@h/db", "LEASE_TTL_S": "abc"})
    assert "hunter2" not in str(caught.value) and "LEASE_TTL_S" in str(caught.value)


@pytest.mark.parametrize("name,raw", [
    ("JUDGE_LIVE_BUDGET_USD", "Infinity"), ("JUDGE_LIVE_BUDGET_USD", "NaN"),
    ("JUDGE_LIVE_BUDGET_USD", "1e9"), ("JUDGE_LIVE_BUDGET_USD", "-5"),
    ("LEASE_TTL_S", "nan"), ("LEASE_TTL_S", "inf"), ("LEASE_TTL_S", "-5"),
    ("MAX_ACTIVE_JOBS", "-1"), ("JOURNAL_TOTAL_BYTES", "-1"),
    ("PROVIDER_DEV_ALLOCATION_CEILING_CREDIT", "-5"),
    ("PROVIDER_DEV_ALLOCATION_CEILING_CREDIT", "NaN"),
    ("PROVIDER_DEV_ALLOCATION_CEILING_CREDIT", "1e3"),
    ("PROVIDER_DEV_ALLOCATION_CEILING_CREDIT", "0.000000001"),
])
def test_nonsense_numbers_are_refused_at_the_boundary(name, raw):
    """A limit that parses as `nan`, `inf` or a negative silently disables itself."""
    with pytest.raises(ValueError, match=name):
        config.pilot_from_env({name: raw})


@pytest.mark.parametrize("name", config.MUST_BE_POSITIVE)
def test_bounds_a_zero_would_disable_are_refused(name):
    pilot = config.pilot_from_env({limits.env_name(name): "0"})
    with pytest.raises(ValueError, match=limits.env_name(name)):
        config.validate_pilot(pilot)


# --- r1 R44: the runtime mode, through the composition root ---------------------
# These build apps with `create_app()` and injected settings. Since the G2 cutover the
# app is the pilot composition, so its adapters are injected too (`_adapters`).
def _app(env, **clients):
    from infrx.gateway.app import create_app
    return create_app(config.from_env({**BUILD, **env}), client=object(), sb=object(),
                      **_adapters(), **clients)


# What install.sh/preflight write for E4B's served-build check (a pilot needs both).
BUILD = {"INFRX_RELEASE_SHA": "c0ffee" + "0" * 34, "INFRX_IMAGE": "sha256:" + "b" * 64}


def _adapters():
    """The pilot composition's adapters as the contract fakes (G2 cutover): a test opens no
    PostgreSQL pool and no Valkey client, and no durable object store has an adapter yet."""
    import asyncio

    from infrx.contracts.conformance.v2_fakes import fake_v2_harness
    from infrx.contracts.fakes.factories import credit_jobstore_factory
    from infrx.media.store import InMemoryObjectStore
    from infrx.scheduling.memory import MemoryScheduler
    harness = credit_jobstore_factory()
    catalog = fake_v2_harness().catalog
    catalog.move_alias(config.from_env({}).model_id,
                       catalog.aliases["nemostation/marlin-2b@2026-09-01"])
    stream = harness.extra["stream"]
    stream.usage = lambda: asyncio.sleep(0, {})       # D4's journal readiness answer
    return {"catalog": catalog, "stream": stream, "objects": InMemoryObjectStore(),
            "jobs": harness.port, "index": MemoryScheduler(harness.clock.now)}


def _validated_as_cutover(env):
    """G1R / E3B dr17: `pilot` refuses while `app.ROUTERS` composes the legacy chat route;
    since the cutover the composition root is the pilot's, so this is the plain hook."""
    return config.validate_runtime(config.from_env(env))


AUTHENTICATED = {"SUPABASE_URL": "https://example.supabase.co",
                 "SUPABASE_SERVICE_ROLE_KEY": "not-a-real-key"}
METERED = {"DATABASE_URL": "postgresql:///x"}


def test_an_unset_mode_refuses_to_start():
    """R44 / F2.2 carryover 14, inverted at the G2 cutover: the legacy F1 entry point is
    retired, so an unset `INFRX_MODE` is a refusal naming the setting, before anything
    mounts - the installer always writes a mode (I0)."""
    assert config.runtime_mode(config.from_env({})) == ""
    with pytest.raises(config.RuntimeMisconfigured) as caught:
        config.validate_runtime(config.from_env({}))
    assert caught.value.missing == ("INFRX_MODE",)
    with pytest.raises(config.RuntimeMisconfigured, match="INFRX_MODE"):
        _app({})


def test_pilot_refuses_to_start_unauthenticated_or_unmetered():
    """R44: `pilot` needs a per-organization identity source *and* a durable store, and
    the error names the missing settings and nothing else."""
    for env, expected in (({"INFRX_MODE": "pilot"},
                           ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")),
                          ({"INFRX_MODE": "pilot", **METERED},
                           ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")),
                          ({"INFRX_MODE": "pilot", **AUTHENTICATED}, ("DATABASE_URL",))):
        with pytest.raises(config.RuntimeMisconfigured) as caught:
            _app(env)
        assert caught.value.missing == expected, env.get("INFRX_MODE")
        for name in expected:
            assert name in str(caught.value)


def test_a_pilot_startup_error_never_echoes_a_value():
    """R44: the message names setting *names*. DATABASE_URL and the service-role key are
    credentials, and a startup error is the most widely pasted line a process emits."""
    secret = "postgresql://user:hunter2@db.internal/infrx"
    with pytest.raises(config.RuntimeMisconfigured) as caught:
        _app({"INFRX_MODE": "pilot", "DATABASE_URL": secret})
    message = str(caught.value)
    assert "hunter2" not in message and secret not in message and "db.internal" not in message
    assert "SUPABASE_URL" in message


def test_pilot_starts_with_authentication_and_metering():
    env = {"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED}
    assert _validated_as_cutover(env) == "pilot"
    # ...and on the composition root itself, which serves chat through the metered ingress.
    app = _app(env)
    assert app.state.runtime.mode == "pilot"
    served = [route.endpoint.__module__ for route in app.routes
              if getattr(route, "path", "") == "/v1/chat/completions"]
    assert served == ["infrx.gateway.routes.ingress"]


def test_pilot_refuses_the_shared_legacy_key(caplog):
    """R51: `pilot` refuses to start with `GATEWAY_API_KEY` set, **even fully configured**.

    `Auth.authenticate` answers `(None, None)` - allowed, with no row - for a request
    bearing the shared key, so it has no organization, no key id and nothing to meter,
    entitle or suspend. That is not a fallback, it is an unmetered anonymous door into a
    metered pilot, and it used to open while validation reported everything in order.
    """
    with pytest.raises(config.RuntimeMisconfigured) as caught:
        _app({"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED,
              "GATEWAY_API_KEY": "one-shared-key"})
    assert caught.value.forbidden == ("GATEWAY_API_KEY",)
    assert caught.value.missing == ()
    # the name, never the value
    assert "GATEWAY_API_KEY" in str(caught.value) and "one-shared-key" not in str(caught.value)
    # and the same answer through the settings-only entry point a track uses
    with pytest.raises(ValueError, match="GATEWAY_API_KEY"):
        config.validate_pilot(
            config.pilot_from_env({"INFRX_MODE": "pilot", **METERED}),
            config.from_env({**AUTHENTICATED, "GATEWAY_API_KEY": "one-shared-key"}))


def test_the_allow_all_path_is_unreachable_in_pilot():
    """R51: with `SUPABASE_URL` required, the other anonymous path is closed too - with
    no Supabase and no legacy key, `authenticate` also answers "allowed, no row"."""
    with pytest.raises(config.RuntimeMisconfigured) as caught:
        _app({"INFRX_MODE": "pilot", **METERED})
    assert "SUPABASE_URL" in caught.value.missing


WHITESPACE = ["", " ", "  ", "\t", "\n", " \t "]


@pytest.mark.parametrize("blank", WHITESPACE, ids=[repr(v) for v in WHITESPACE])
@pytest.mark.parametrize("name", ["DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_a_whitespace_only_setting_is_not_configuration(name, blank):
    """R51: `SUPABASE_URL=" "` passed a truthiness test and then built a client pointed at
    `" /rest/v1"`, so the pilot started "authenticated" against nothing. A unit file makes
    a stray space easy to write and impossible to see."""
    env = {"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED, name: blank}
    with pytest.raises(config.RuntimeMisconfigured) as caught:
        _app(env)
    assert name in caught.value.missing, caught.value.missing


def test_a_whitespace_only_legacy_key_is_not_a_legacy_key():
    """The same rule on the forbidden side: `GATEWAY_API_KEY=" "` is unset, not a shared
    key, so it must not block a correctly configured pilot."""
    assert _validated_as_cutover({"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED,
                                  "GATEWAY_API_KEY": "  "}) == "pilot"


@pytest.mark.parametrize("mode", ["pilo", "PILOT", "production", "legacy", "dev "])
def test_an_unrecognised_mode_refuses_to_start(mode):
    """A typo in a unit file is not a mode. `legacy` is spelled by *absence*, so it is
    refused as a value too."""
    with pytest.raises(config.RuntimeMisconfigured):
        _app({"INFRX_MODE": mode})


@pytest.mark.parametrize("mode", ["dev", "test"])
def test_dev_and_test_are_explicit_and_need_nothing_else(mode):
    app = _app({"INFRX_MODE": mode})
    assert app.state.runtime.mode == mode


def test_the_router_list_is_fixed_and_uses_the_register_protocol():
    """R44: track routers are modules exposing `register(app, rt)`, added to this literal
    by the coordinator on an integration request. A discovery walk would let a
    half-finished track mount itself on the public gateway."""
    from infrx.gateway import app as composition_root
    assert [module.__name__.rsplit(".", 1)[-1] for module in composition_root.ROUTERS] == \
        ["health", "models", "ingress", "uploads", "jobs", "route"]
    for module in composition_root.ROUTERS:
        assert callable(getattr(module, "register"))


def test_f1_gateway_settings_are_untouched():
    """F-BASE: the pilot names are additive; the F1 gateway defaults do not move."""
    gateway = config.from_env({})
    assert (gateway.upstream, gateway.max_inflight, gateway.fetch_timeout_s) == \
        ("http://127.0.0.1:8000", 16, 30.0)
    assert gateway.max_video_mb == 64.0 and gateway.max_redirects == 3


# --- import boundary ---------------------------------------------------------
HEAVY = ("psycopg", "valkey", "clickhouse_connect", "boto3", "anthropic")
# Walk the package rather than listing modules by hand: a module added later is
# covered without anyone remembering to add it here (F1 post-merge follow-up).
# `gateway` is excluded because it is the F1 entry point, not part of the package.
PROBE = """
import importlib, pkgutil, sys
import infrx
walked = []
for info in pkgutil.walk_packages(infrx.__path__, prefix="infrx."):
    importlib.import_module(info.name)
    walked.append(info.name)
if len(walked) < 10:
    raise SystemExit("walk_packages found almost nothing: " + repr(walked))
leaked = sorted({m.split('.')[0] for m in sys.modules} & set(%r))
print(repr((leaked, len(walked))))
"""


def test_contracts_import_pulls_in_no_track_dependency():
    """A gateway process must not pay for a track's extra: importing *every* module
    in the `infrx` package - contracts, fakes, conformance, fixtures, the F1 modules -
    loads none of psycopg, valkey, clickhouse-connect, boto3 or anthropic."""
    completed = subprocess.run([sys.executable, "-c", PROBE % (HEAVY,)],
                               capture_output=True, text=True, cwd=str(_api_dir()))
    assert completed.returncode == 0, completed.stderr
    leaked, walked = eval(completed.stdout.strip())      # our own literal, two ints deep
    assert leaked == [], leaked
    assert walked >= 20, f"only {walked} modules were walked"


def test_contracts_v2_resolves_by_attribute_access_like_every_submodule():
    """F2P wire-in item 1: `infrx.contracts.v2` is a listed submodule, reached lazily by
    attribute access and named by `dir()`, so a consumer never needs an import path that
    differs from v1's."""
    import infrx.contracts as contracts
    assert "v2" in dir(contracts)
    assert contracts.v2.SURFACE_VERSION == "contracts-v2.0"
    assert contracts.v2.records.SCHEMA_VERSION == 2


def test_the_extras_are_installed_so_the_check_is_meaningful():
    """Otherwise the test above would pass by accident in a core-only environment.

    A **failure**, not a skip. This used to skip, which meant the one test that gives the
    import-boundary check its meaning could go quiet and `make api-test` would still print
    a clean pass: an environment without the extras cannot prove that importing `infrx`
    leaves them unimported, because there is nothing to leave unimported. `make api-env`
    (`uv sync --frozen --all-extras`) is what the canonical command depends on, and
    `uv run --frozen` keeps them, so this failing means the environment is wrong rather
    than the code.
    """
    import importlib.util
    missing = [name for name in HEAVY if importlib.util.find_spec(name) is None]
    assert missing == [], (
        f"the import-boundary check cannot run: {', '.join(missing)} not installed. "
        f"Run `make api-env` (uv sync --frozen --all-extras) - a skip here would let "
        f"`make api-test` report a pass for a check that never ran.")


def test_task_local_services_never_collide_across_worktrees():
    """08 §8: one container name, port, database and object prefix per task."""
    assert tasklocal.all_host_ports()[55432] == "d1/postgres"
    d1 = tasklocal.local_services("D1")["postgres"]
    assert (d1.container, d1.host_port, d1.database, d1.object_prefix) == \
        ("infrx-d1-postgres", 55432, "infrx_d1", "test/d1/")
    assert tasklocal.local_services("d2")["postgres"].container == "infrx-d2-postgres"


# r1 R48: PostgreSQL is per **task**. More than one of these is open at once in practice -
# a coordinator running D2's migration while C1's console suite holds a database - and
# they all used to be handed 55432, so the second one silently talked to the first's data.
R48_POSTGRES_PORTS = {"d1": 55432, "d2": 55433, "d3": 55434, "d5": 55436, "d4": 55435,
                      "d6": 55437, "c1": 55441}


@pytest.mark.parametrize("task,port", sorted(R48_POSTGRES_PORTS.items()))
def test_each_database_task_has_its_own_postgres_port(task, port):
    service = tasklocal.local_services(task)["postgres"]
    assert service.host_port == port
    assert service.container == f"infrx-{task}-postgres"
    assert service.database == f"infrx_{task}"


def test_no_two_tasks_share_a_postgres_port():
    ports = {task: tasklocal.local_services(task)["postgres"].host_port
             for task in R48_POSTGRES_PORTS}
    assert len(set(ports.values())) == len(ports), ports
    reserved = tasklocal.all_host_ports()
    for task, port in ports.items():
        assert reserved[port] == f"{task}/postgres"
    # and the whole table is still collision-free, which `all_host_ports` enforces
    assert len(reserved) == len(set(reserved))


def test_c1_is_the_only_console_task_with_a_database():
    """R48 grants C1 a port; the rest of C, and U/V, build against the fakes."""
    assert tasklocal.local_services("c1")["postgres"].host_port == 55441
    assert tasklocal.local_services("c2") == {}
    assert tasklocal.local_services("u1") == {} and tasklocal.local_services("v1") == {}
    assert sorted(tasklocal.local_services("t")) == ["clickhouse", "s3"]
    assert tasklocal.local_services("t")["s3"].host_port != \
        tasklocal.local_services("m")["s3"].host_port
    assert tasklocal.local_services("g3") == {}          # fakes until integration
    with pytest.raises(ValueError):
        tasklocal.local_services("z9")


def _api_dir():
    import pathlib
    return pathlib.Path(__file__).resolve().parents[2]


# --- F2R item 7: the 08 §5 deployment table ------------------------------------------
# Name by name, like `EXPECTED` above. These live in `config.DeploymentSettings` rather
# than in `contracts.limits.PilotSettings` because they are deployment knobs, not numbers
# both language halves enforce; the split is recorded in 08 §5 and in config.py.
DEPLOYMENT_EXPECTED = {
    "TRACE_SPOOL_SEGMENT_BYTES": 16777216,
    "ACCOUNTING_REGIME": "legacy_usd",
    "CONSOLE_CURSOR_SECRET": "",
    "DATABASE_POOL_MIN_SIZE": 1, "DATABASE_POOL_MAX_SIZE": 10,
    "DATABASE_POOL_CONNECT_TIMEOUT_S": 5.0,
    "DATABASE_POOL_STATEMENT_TIMEOUT_MS": 15000,
    "MAX_MESSAGES": 64, "MAX_PARTS": 16, "MAX_TEXT_CODEPOINTS": 131072,
    "MAX_URL_CHARS": 8192, "MAX_NUMBER_DIGITS": 20,
    "LARGE_BODY_LIMIT": 2, "LARGE_BODY_THRESHOLD_BYTES": 1048576,
    # M1-L2: the media object store's place in S3_MEDIA_BUCKET, and an S3-compatible endpoint
    "S3_MEDIA_PREFIX": "infrx/", "S3_ENDPOINT_URL": "",
    # E4B: the deployed commit and image, `infrx_build_info` (required in pilot at startup)
    "INFRX_RELEASE_SHA": "", "INFRX_IMAGE": "",
}

# Everything except the text values (the secret, the accounting regime).
DEPLOYMENT_NUMBERS = tuple(name for name, value in sorted(DEPLOYMENT_EXPECTED.items())
                           if not isinstance(value, str))


def test_every_deployment_name_and_default_is_frozen():
    actual = {limits.env_name(f.name): getattr(config.DEPLOYMENT_DEFAULTS, f.name)
              for f in dataclasses.fields(config.DeploymentSettings)}
    assert actual == DEPLOYMENT_EXPECTED


def test_the_deployment_names_are_nobodys_existing_names():
    """A deployment name that collided with an F1 or pilot variable would let one
    setting silently retune the other."""
    f1 = {"FETCH_TIMEOUT_S", "MAX_VIDEO_MB", "MAX_REDIRECTS", "MAX_INFLIGHT", "UPSTREAM",
          "GATEWAY_API_KEY", "USAGE_LOG", "ALLOWED_VIDEO_MIME", "MODEL_ID", "MODELS_DOC"}
    assert set(DEPLOYMENT_EXPECTED).isdisjoint(set(EXPECTED))
    assert set(DEPLOYMENT_EXPECTED).isdisjoint(f1)


@pytest.mark.parametrize("name", sorted(DEPLOYMENT_EXPECTED))
def test_each_deployment_name_is_read_from_the_environment(name):
    raw = "s" * 32 if name == "CONSOLE_CURSOR_SECRET" else "7"
    deployment = config.deployment_from_env({name: raw})
    field_name = name.lower()
    kind = type(getattr(config.DEPLOYMENT_DEFAULTS, field_name))
    assert getattr(deployment, field_name) == kind(raw)


@pytest.mark.parametrize("name", sorted(DEPLOYMENT_EXPECTED))
def test_a_missing_deployment_name_takes_its_default(name):
    """Absent is unset, and unset is the frozen default -- for this name only: the read
    must not disturb any other field."""
    others = {n: v for n, v in DEPLOYMENT_EXPECTED.items() if n != name}
    deployment = config.deployment_from_env({n: str(v) for n, v in others.items() if v != ""})
    assert getattr(deployment, name.lower()) == DEPLOYMENT_EXPECTED[name]


@pytest.mark.parametrize("name", sorted(DEPLOYMENT_EXPECTED))
def test_an_empty_deployment_value_is_refused(name):
    """`MAX_MESSAGES=` in a unit file is a mistake. Serving the default for it is how an
    operator comes to believe they configured something they did not."""
    for blank in ("", " ", "\t"):
        with pytest.raises(ValueError, match=name):
            config.deployment_from_env({name: blank})


@pytest.mark.parametrize("name", DEPLOYMENT_NUMBERS)
def test_a_deployment_value_of_the_wrong_type_is_refused(name):
    with pytest.raises(ValueError, match=name):
        config.deployment_from_env({name: "abc"})
    with pytest.raises(ValueError, match=name):
        config.deployment_from_env({name: "-1"})


@pytest.mark.parametrize("name", DEPLOYMENT_NUMBERS)
def test_a_deployment_bound_a_zero_would_disable_is_refused(name):
    """Every number here is a size or a cap: zero disables it rather than tightening it.
    A zero pool admits no connection, a zero statement timeout means *no* limit in
    PostgreSQL, a zero message cap refuses every request, a zero segment never rotates."""
    assert name.lower() in config.DEPLOYMENT_MUST_BE_POSITIVE
    with pytest.raises(config.RuntimeMisconfigured, match=name):
        config.validate_deployment(config.deployment_from_env({name: "0"}))


def test_the_pool_bounds_must_be_ordered():
    with pytest.raises(config.RuntimeMisconfigured, match="DATABASE_POOL_MIN_SIZE"):
        config.validate_deployment(config.deployment_from_env(
            {"DATABASE_POOL_MIN_SIZE": "11", "DATABASE_POOL_MAX_SIZE": "10"}))
    assert config.validate_deployment(config.deployment_from_env(
        {"DATABASE_POOL_MIN_SIZE": "10", "DATABASE_POOL_MAX_SIZE": "10"}))


def test_a_short_cursor_secret_is_refused_without_echoing_it():
    """It is a signing key, and a startup error is the most widely copied line of text a
    process ever emits."""
    secret = "tooshort"
    with pytest.raises(config.RuntimeMisconfigured) as caught:
        config.validate_deployment(config.deployment_from_env({"CONSOLE_CURSOR_SECRET": secret}))
    assert "CONSOLE_CURSOR_SECRET" in str(caught.value) and secret not in str(caught.value)
    long_enough = "s" * config.MIN_CONSOLE_CURSOR_SECRET_CHARS
    assert config.validate_deployment(config.deployment_from_env(
        {"CONSOLE_CURSOR_SECRET": long_enough})).console_cursor_secret == long_enough


def test_the_cursor_secret_bound_is_exactly_sixteen_characters():
    """F2R-B NB-3: the boundary itself. Fifteen characters is refused and sixteen is
    accepted, so a bound that drifts by one in either direction fails here (the case above
    only proves that an 8-character secret is refused)."""
    assert config.MIN_CONSOLE_CURSOR_SECRET_CHARS == 16
    refused = False
    try:
        config.validate_deployment(config.deployment_from_env({"CONSOLE_CURSOR_SECRET": "s" * 15}))
    except config.RuntimeMisconfigured:
        refused = True
    assert refused, "a 15-character cursor secret was accepted"
    # F2P review CFG-6: the bound is measured after stripping, so padding cannot make a
    # 14-character key look like 16.
    # CONF-N3: and padding on one side only cannot either.
    for padded in (" " + "s" * 14 + " ", "  " + "s" * 14, "s" * 14 + "  "):
        with pytest.raises(config.RuntimeMisconfigured, match="CONSOLE_CURSOR_SECRET"):
            config.validate_deployment(config.deployment_from_env(
                {"CONSOLE_CURSOR_SECRET": padded}))
    try:
        config.validate_deployment(config.deployment_from_env({"CONSOLE_CURSOR_SECRET": "s" * 16}))
    except config.RuntimeMisconfigured:
        raise AssertionError("a 16-character cursor secret was refused") from None


@pytest.mark.parametrize("regime", ["legacy_usd", "credit"])
def test_the_accounting_regime_is_a_v2_regime(regime):
    """F2P wire-in item 5: ACCOUNTING_REGIME is one of the two v2 regimes; v1's
    `pilot`, a case variant and a typo are refused before anything mounts."""
    assert config.validate_deployment(config.deployment_from_env(
        {"ACCOUNTING_REGIME": regime})).accounting_regime == regime
    for bad in ("pilot", "CREDIT", "credits"):
        refused = False
        try:
            config.validate_deployment(config.deployment_from_env({"ACCOUNTING_REGIME": bad}))
        except config.RuntimeMisconfigured as caught:
            refused = "ACCOUNTING_REGIME" in str(caught)
        assert refused, f"ACCOUNTING_REGIME={bad!r} was accepted"


def test_a_credit_deployment_needs_an_approved_rate_card():
    """R69 at startup: the CREDIT regime without ACTIVE_RATE_CARD_VERSION refuses to start
    in every mode, naming the setting; with one it starts; the legacy regime needs none."""
    for mode in ("", "dev", "pilot"):
        env = {"ACCOUNTING_REGIME": "credit", **({"INFRX_MODE": mode} if mode else {})}
        if mode == "pilot":
            env.update(METERED, **AUTHENTICATED)
        refused = False
        try:
            _app(env)
        except config.RuntimeMisconfigured as caught:
            refused = "ACTIVE_RATE_CARD_VERSION" in str(caught)
        assert refused, f"mode {mode!r} started a CREDIT deployment with no approved card"
        # G1R review C1: in pilot the app refuses to start on the legacy composition (dr17),
        # so the corrected card is validated as the cutover composes it.
        corrected = {**env, "ACTIVE_RATE_CARD_VERSION": "rc_marlin2b_2026_09_provisional"}
        if mode == "pilot":
            assert _validated_as_cutover(corrected) == "pilot"
        elif mode:
            assert _app(corrected)
        else:                   # the card passes; an unset mode is refused since the cutover
            with pytest.raises(config.RuntimeMisconfigured, match="requires INFRX_MODE"):
                _app(corrected)
        # F2P review M-6/CFG-2: whitespace is not a card (refused as missing), and a padded
        # name is refused at startup rather than served (`validate_pilot` is off this path).
        with pytest.raises(config.RuntimeMisconfigured, match="requires ACTIVE_RATE_CARD_VERSION"):
            _app({**env, "ACTIVE_RATE_CARD_VERSION": "  "})
        # CONF-N1: padding on one side only is padding too (a one-sided strip must not pass).
        for padded in (" rc_marlin2b_2026_09_provisional ", "rc_marlin2b_2026_09_provisional ",
                       " rc_marlin2b_2026_09_provisional"):
            with pytest.raises(config.RuntimeMisconfigured, match="ACTIVE_RATE_CARD_VERSION must not"):
                _app({**env, "ACTIVE_RATE_CARD_VERSION": padded})
    assert _app({"INFRX_MODE": "dev", "ACCOUNTING_REGIME": "legacy_usd"}) is not None
    # F2P confirmation CONF-N2: whitespace-only is unset in every regime, so a legacy
    # deployment starts; a padded card is refused in every regime (the rule is the text's).
    try:
        started = _app({"INFRX_MODE": "dev", "ACCOUNTING_REGIME": "legacy_usd",
                        "ACTIVE_RATE_CARD_VERSION": "  "})
    except config.RuntimeMisconfigured as refused:
        raise AssertionError(f"a whitespace-only card was read as a card: {refused}") from None
    assert started is not None
    with pytest.raises(config.RuntimeMisconfigured, match="ACTIVE_RATE_CARD_VERSION must not"):
        _app({"ACCOUNTING_REGIME": "legacy_usd", "ACTIVE_RATE_CARD_VERSION": " rc_x "})


def test_the_allocation_ceiling_is_a_credit_amount_defaulting_to_nothing():
    """The provider-dev allocation ceiling is a `Credit` (a unit is a type, R64): zero by
    default - no allocation until an operator sets one - and parsed by the money rules."""
    default = limits.DEFAULTS.provider_dev_allocation_ceiling_credit
    assert type(default) is Credit and str(default) == "0.00000000"
    parsed = config.pilot_from_env({"PROVIDER_DEV_ALLOCATION_CEILING_CREDIT": "250.5"})
    assert parsed.provider_dev_allocation_ceiling_credit == Credit("250.5")


def test_the_active_rate_card_version_is_exact_text():
    for bad in (" rc_x", "rc_x ", "rc_x\n"):
        with pytest.raises(ValueError, match="ACTIVE_RATE_CARD_VERSION"):
            config.validate_pilot(limits.DEFAULTS.replace(active_rate_card_version=bad))
    assert config.validate_pilot(limits.DEFAULTS.replace(active_rate_card_version="rc_x"))


def test_the_cursor_secret_is_the_consoles_requirement_and_not_this_gateways():
    """Unset, it is not a gateway startup failure in any mode, including `pilot`: this
    process serves no console page, so it has nothing to sign, and refusing to serve
    inference over a console setting would be the wrong coupling. The requirement is
    recorded for C2 and the deployment checklist instead."""
    assert config.CONSOLE_ONLY_SETTINGS == ("CONSOLE_CURSOR_SECRET",)
    assert config.DEPLOYMENT_DEFAULTS.console_cursor_secret == ""
    assert _validated_as_cutover({"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED}) == "pilot"


@pytest.mark.parametrize("setting", ["MAX_MESSAGES", "MAX_INDEX_ITEMS"])
@pytest.mark.parametrize("mode", ["", "dev", "test", "pilot"])
def test_a_bad_deployment_value_refuses_before_anything_mounts(mode, setting):
    """`create_app` validates before it builds the app, so a bad value is a startup
    failure in every mode rather than a surprise on the first request that reaches the
    setting. No app object exists to serve, which is the observable form of "nothing
    mounted"."""
    env = {setting: "0"}
    if mode:
        env["INFRX_MODE"] = mode
    if mode == "pilot":
        env.update(METERED, **AUTHENTICATED)
    with pytest.raises(config.RuntimeMisconfigured, match=setting):
        _app(env)
    # And the same configuration with the value corrected does start, so the refusal is
    # about the value and not about the mode (pilot: as the cutover composes it).
    if mode == "pilot":
        assert _validated_as_cutover({**env, setting: "64"}) == "pilot"
    elif mode:
        assert _app({**env, setting: "64"}) is not None
    else:                       # the value passes; an unset mode is refused since the cutover
        with pytest.raises(config.RuntimeMisconfigured, match="requires INFRX_MODE"):
            _app({**env, setting: "64"})


def test_a_zero_index_cap_refuses_to_start():
    """F2P review M-3: the two scheduler index caps moved from the deployment table to
    `PilotSettings` (item 5) and kept their startup refusal: a zero cap would reach Q2's
    adapter and disable the bound. Named here, not derived from `MUST_BE_POSITIVE`, so
    dropping a name from that list fails this case."""
    for name in ("MAX_INDEX_ITEMS", "MAX_INDEX_BYTES"):
        with pytest.raises(config.RuntimeMisconfigured, match=name):
            _app({name: "0"})


def test_the_g1_and_q1_constants_match_the_deployment_defaults():
    """The caps still live as module constants in G1's validator and Q1's scheduler; the
    wiring is an integration request for those owners. Until it lands, this is what keeps
    the two from drifting: a default changed here without the constant (or the reverse) is
    two different limits with one name in 08 §5.
    """
    from infrx.gateway.routes import intake, validate
    from infrx.scheduling import memory
    d = config.DEPLOYMENT_DEFAULTS
    assert validate.MAX_MESSAGES == d.max_messages
    assert validate.MAX_PARTS_PER_MESSAGE == d.max_parts
    assert validate.MAX_TEXT_CODEPOINTS == d.max_text_codepoints
    assert validate.MAX_URL_CHARS == d.max_url_chars
    assert intake.MAX_NUMBER_DIGITS == d.max_number_digits
    # Q2 request 3: the index caps are pilot settings now (the Valkey adapter reads them).
    assert memory.MAX_INDEX_ITEMS == limits.DEFAULTS.max_index_items
    assert memory.MAX_INDEX_BYTES == limits.DEFAULTS.max_index_bytes
    # `LargeBodies` states its two numbers as parameter defaults rather than constants.
    slots = intake.LargeBodies()
    assert slots.limit == d.large_body_limit
    assert slots.threshold == d.large_body_threshold_bytes
