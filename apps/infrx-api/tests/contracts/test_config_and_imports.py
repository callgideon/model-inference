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
    "INFRX_MODE": "dev", "DATABASE_URL": "",
    "MAX_REQUEST_BYTES": 100663296, "INTAKE_TIMEOUT_S": 30.0,
    "MAX_MEDIA_BYTES": 67108864, "MAX_VIDEO_SECONDS": 120.0,
    "FETCH_CONNECT_TIMEOUT_S": 3.0, "FETCH_TIMEOUT_S": 20.0,
    "FETCH_MAX_REDIRECTS": 3, "PROBE_TIMEOUT_S": 10.0,
    "PREPARATION_TIMEOUT_S": 120.0, "TRANSCODE_MIN_TIMEOUT_S": 15.0,
    "TRANSCODE_DURATION_FACTOR": 0.5, "PREPARATION_CONCURRENCY": 2,
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
    # refinement: the 24h window 02 requires before an unknown-usage hold is freed
    "UNKNOWN_USAGE_RECONCILE_S": 86400.0,
}


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


def test_f1_gateway_settings_are_untouched():
    """F-BASE: the pilot names are additive; the F1 gateway defaults do not move."""
    gateway = config.from_env({})
    assert (gateway.upstream, gateway.max_inflight, gateway.fetch_timeout_s) == \
        ("http://127.0.0.1:8000", 16, 30.0)
    assert gateway.max_video_mb == 64.0 and gateway.max_redirects == 3


# --- import boundary ---------------------------------------------------------
HEAVY = ("psycopg", "valkey", "clickhouse_connect", "boto3", "anthropic")
PROBE = """
import sys
import infrx.contracts
from infrx.contracts import errors, fixtures, ids, limits, money, ports, records
from infrx.contracts import tasklocal, wire
from infrx.contracts import conformance
from infrx.contracts import fakes
leaked = sorted({m.split('.')[0] for m in sys.modules} & set(%r))
print(leaked)
"""


def test_contracts_import_pulls_in_no_track_dependency():
    """A gateway process must not pay for a track's extra: importing the contracts,
    the fakes and the conformance suites loads none of them."""
    completed = subprocess.run([sys.executable, "-c", PROBE % (HEAVY,)],
                               capture_output=True, text=True, cwd=str(_api_dir()))
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]", completed.stdout


def test_the_extras_are_installed_so_the_check_is_meaningful():
    """Otherwise the test above would pass by accident in a core-only environment."""
    import importlib.util
    missing = [name for name in HEAVY if importlib.util.find_spec(name) is None]
    if missing:
        pytest.skip(f"not installed (run uv sync --all-extras): {', '.join(missing)}")


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
