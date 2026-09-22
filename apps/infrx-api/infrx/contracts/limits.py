"""Frozen configuration names, defaults and derived limits (08 §5).

Pure data: this module reads no environment. `infrx/config.py` is the only
environment reader and builds a `PilotSettings` from these defaults, so a track
can import the numbers without importing process state.

The environment variable for a field is its name uppercased
(`journal_total_bytes` -> `JOURNAL_TOTAL_BYTES`); `env_name`/`fields_by_env`
make that mapping explicit rather than assumed.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, fields
from decimal import Decimal

MODES = ("dev", "test", "pilot")
# r1 R44: `INFRX_MODE` has **no default**. The empty string is "the variable is not
# set", which `config.validate_runtime` maps to the legacy F1 behaviour F1 preserved by
# rule; G1 replaces "unset -> legacy" with "unset -> refuse" at cutover, in the same
# change in which I2's installer writes `INFRX_MODE=pilot`.
MODE_UNSET = ""
JUDGE_MODES = ("dry_run", "live")

# --- shared input bounds (R17, R43) ------------------------------------------
# Contract data, not tunables: these are the *same numbers* in both halves, and
# `tests/contracts/test_parity_console.py` parses the console's constants and
# compares them here. They are deliberately not `PilotSettings` fields, because
# no environment variable may widen a bound another language enforces.
MAX_IDEMPOTENCY_KEY_CHARS = 255          # 08 §3
MAX_PAGE_LIMIT = 100                     # 08 §9: a hard bound, not a clamp
MAX_KEY_NAME_CHARS = 200                 # R17
MAX_GRANT_REASON_CHARS = 500             # R17
MAX_FEEDBACK_TEXT_CHARS = 4_000          # R43
MIN_RUBRIC_VERSION = 1                   # R43: an integer everywhere
MAX_RUBRIC_VERSION = 1_000
MIN_CONTENT_RETENTION_DAYS = 1           # R43: retention is 1..90, never 0
MAX_CONTENT_RETENTION_DAYS = 90
MAX_ENTITLEMENT_LIMIT = 1_000_000        # so a typo cannot mean "unlimited"

# R24: the closed set of per-organization entitlement limit names. An unknown
# name is `invalid_request`, never a silently ignored control.
ENTITLEMENT_LIMIT_NAMES = ("max_concurrent_requests", "max_requests_per_minute",
                           "max_video_seconds")


@dataclass(frozen=True)
class PilotSettings:
    """Provisional engineering limits until measured; values are contracts v1's."""

    # r1 R44: no default. Unset is unset, not `dev`, because a mode with a default is a
    # production host that silently runs in whatever the default happens to be.
    infrx_mode: str = MODE_UNSET
    database_url: str = ""                       # required in pilot

    # intake and media
    max_request_bytes: int = 100_663_296         # 96 MiB
    intake_timeout_s: float = 30.0
    max_media_bytes: int = 67_108_864            # 64 MiB decoded
    max_video_seconds: float = 120.0
    # r1 R2: the new media path's own names. F1's FETCH_TIMEOUT_S (30),
    # MAX_VIDEO_MB and MAX_REDIRECTS keep their names and defaults until M1
    # retires them, so the two paths never read the same variable.
    media_fetch_connect_timeout_s: float = 3.0
    media_fetch_timeout_s: float = 20.0
    media_fetch_max_redirects: int = 3
    probe_timeout_s: float = 10.0

    # preparation
    preparation_timeout_s: float = 120.0
    transcode_min_timeout_s: float = 15.0
    transcode_duration_factor: float = 0.5
    preparation_concurrency: int = 2             # r1 R1: host worker-pool size only
    max_preparing_jobs: int = 8                  # r1 R1: the admission-side cap

    # queue, generation, lease
    queue_wait_interactive_s: float = 10.0
    queue_wait_async_s: float = 600.0
    generation_timeout_s: float = 300.0
    ttft_timeout_s: float = 60.0
    tpot_stall_s: float = 20.0
    lease_ttl_s: float = 120.0
    # r1 R52: preparation gets a **shorter** lease than inference. With one
    # 120 s TTL and a 120 s preparation budget, a lost preparation worker was
    # only reaped as the phase deadline passed, so R46's bounded retries could
    # never actually happen on the default profile. 30 s leaves room for three.
    preparation_lease_ttl_s: float = 30.0
    lease_heartbeat_s: float = 40.0
    max_prepublication_retries: int = 2

    # output journal
    sse_keepalive_s: float = 10.0
    stream_batch_ms: int = 50
    journal_event_max_bytes: int = 1_048_576
    journal_job_reserve_bytes: int = 16_777_216
    journal_total_bytes: int = 1_073_741_824
    journal_chunk_ttl_s: float = 3_600.0

    # retention
    result_ttl_s: float = 86_400.0
    processing_cache_ttl_s: float = 604_800.0
    # M2's local processing cache root (R61 (2)): unset means no local cache
    processing_cache_dir: str = ""
    idempotency_ttl_s: float = 86_400.0          # after terminal
    trace_content_max_days: int = 90
    trace_metadata_months: int = 13

    # capacity
    max_active_jobs: int = 64
    max_active_jobs_per_org: int = 16
    max_active_jobs_per_key: int = 8
    engine_max_num_seqs: int = 8
    worker_concurrency: int = 10
    max_output_tokens: int = 2_048
    max_context_tokens: int = 32_768

    # traces
    trace_capture_bytes: int = 268_435_456       # 256 MiB in-process
    trace_metadata_reserve_bytes: int = 8_388_608
    trace_queue_max: int = 10_000
    trace_spool_dir: str = ""                    # unset disables capture
    trace_spool_max_bytes: int = 10_737_418_240
    trace_spool_min_free_bytes: int = 2_147_483_648
    trace_fsync_interval_s: float = 2.0

    # judge
    judge_mode: str = "dry_run"
    judge_live_budget_usd: Decimal = Decimal("0")

    # optional services (unset = the in-memory/disabled path)
    valkey_url: str = ""
    clickhouse_url: str = ""
    s3_media_bucket: str = ""
    s3_trace_bucket: str = ""

    # unknown-usage reconciliation window (02: release only after 24h + fencing;
    # named by r1 R14 and part of 08 §5)
    unknown_usage_reconcile_s: float = 86_400.0

    def replace(self, **changes: object) -> PilotSettings:
        return dataclasses.replace(self, **changes)


DEFAULTS = PilotSettings()


def env_name(field_name: str) -> str:
    return field_name.upper()


def fields_by_env() -> dict[str, dataclasses.Field]:
    return {env_name(f.name): f for f in fields(PilotSettings)}


ENV_NAMES = tuple(sorted(fields_by_env()))
