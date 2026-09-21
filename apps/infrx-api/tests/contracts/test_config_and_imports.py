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
    "SSE_KEEPALIVE_S": 10.0, "STREAM_BATCH_MS": 50,
    "JOURNAL_EVENT_MAX_BYTES": 1048576, "JOURNAL_JOB_RESERVE_BYTES": 16777216,
    "JOURNAL_TOTAL_BYTES": 1073741824, "JOURNAL_CHUNK_TTL_S": 3600.0,
    "RESULT_TTL_S": 86400.0, "PROCESSING_CACHE_TTL_S": 604800.0,
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
           "TRACE_SPOOL_DIR": "/tmp/spool"}.get(name, "7")
    pilot = config.pilot_from_env({name: raw})
    field_name = limits.fields_by_env()[name].name
    kind = type(getattr(limits.DEFAULTS, field_name))
    assert getattr(pilot, field_name) == kind(raw)


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
# These build apps with `create_app()` and injected settings. They never import the
# legacy `gateway` shim: it mutates process state at import and directories collect
# before the top-level legacy files, so importing it here would reorder the suite (R48).
def _app(env, **clients):
    from infrx.gateway.app import create_app
    return create_app(config.from_env(env), client=object(), sb=object(), **clients)


AUTHENTICATED = {"SUPABASE_URL": "https://example.supabase.co",
                 "SUPABASE_SERVICE_ROLE_KEY": "not-a-real-key"}
METERED = {"DATABASE_URL": "postgresql:///x"}


def test_an_unset_mode_is_the_legacy_f1_behaviour():
    """R44: while `INFRX_MODE` is unset the app starts exactly as F1's did, and says so.

    F1 preserved behaviour by rule, so the legacy entry point cannot start refusing;
    G1 replaces this branch with "unset -> refuse" at cutover.
    """
    assert config.runtime_mode(config.from_env({})) == ""
    assert config.validate_runtime(config.from_env({})) == "legacy"
    app = _app({})
    assert app.state.runtime.mode == "legacy"
    # and the F1 routes are mounted, i.e. "legacy" is the whole app, not a stub
    paths = {route.path for route in app.routes}
    assert {"/health", "/v1/models", "/v1/chat/completions"} <= paths


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
    app = _app({"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED})
    assert app.state.runtime.mode == "pilot"


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
    app = _app({"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED, "GATEWAY_API_KEY": "  "})
    assert app.state.runtime.mode == "pilot"


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
        ["health", "models", "chat"]
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
    assert tasklocal.all_host_ports()[55432] == "d/postgres"
    d1 = tasklocal.local_services("D1")["postgres"]
    assert (d1.container, d1.host_port, d1.database, d1.object_prefix) == \
        ("infrx-d1-postgres", 55432, "infrx_d1", "test/d1/")
    assert tasklocal.local_services("d2")["postgres"].container == "infrx-d2-postgres"
    assert sorted(tasklocal.local_services("t")) == ["clickhouse", "s3"]
    assert tasklocal.local_services("t")["s3"].host_port != \
        tasklocal.local_services("m")["s3"].host_port
    assert tasklocal.local_services("g3") == {}          # fakes until integration
    with pytest.raises(ValueError):
        tasklocal.local_services("z9")


def _api_dir():
    import pathlib
    return pathlib.Path(__file__).resolve().parents[2]
