"""AP-08 (api-judge-2): the judge role's composition (`python -m infrx.lab.workers judge`'s
`compose`) - the START job exists only with AP-07's eligible read composed, egress beyond
loopback only to an https host the operator allowlisted AND only in live mode (P-10), and the
gold set is a reviewed reference set or the role refuses to start. No database is reached
(the connector is lazy); T3's retention is replaced as `tests/j/calibration`'s composition
test replaces it. The process itself is `test_judge_cli_pg.py`.
"""
from __future__ import annotations

import pytest

from infrx.config import RuntimeMisconfigured
from infrx.judge import start
from infrx.lab.workers import __main__ as lab_workers

BASE = {"LAB_DATABASE_URL": "postgresql://ap8@127.0.0.1:9/none", "LAB_WORKER_HEALTH_PORT": "9",
        "JUDGE_PROVIDER_URL": "http://127.0.0.1:9", "CLICKHOUSE_URL": "http://ch.invalid:8123/x",
        "S3_TRACE_BUCKET": "unused"}
LIVE = {"JUDGE_MODE": "live", "JUDGE_LIVE_BUDGET_USD": "1"}
REMOTE = {"JUDGE_PROVIDER_URL": "https://judge.example.com"}


@pytest.fixture(autouse=True)
def no_trace_store(monkeypatch):
    monkeypatch.setattr(lab_workers, "trace_retention",
                        lambda limits, url, objects=None, connect=None: object())


def compose(**env):
    return lab_workers.compose("judge", {**BASE, **env})


def test_ap08_role__the_start_job_needs_the_eligible_read(monkeypatch):
    """Failure oracle: a start job over no trace read (it would judge nothing, or guess), or
    none when the read is composed (queued runs never start)."""
    assert "judge_start" not in compose().tasks
    async def eligible(*_):
        return []
    monkeypatch.setattr(start, "eligible_read", lambda limits: eligible)
    assert set(compose().tasks) == {"judge_sweep", "judge_collect", "judge_start"}


def test_ap08_role__a_remote_judge_is_an_allowlisted_https_host_in_live_mode_only():
    """Failure oracle: egress to a host nobody approved, an approved host reached in dry-run,
    an allowlist that is not bare host names."""
    with pytest.raises(RuntimeMisconfigured):
        compose(**REMOTE, **LIVE)
    with pytest.raises(RuntimeMisconfigured):
        compose(**REMOTE, JUDGE_PROVIDER_ALLOWLIST="judge.example.com")
    with pytest.raises(RuntimeMisconfigured):
        compose(**REMOTE, **LIVE, JUDGE_PROVIDER_ALLOWLIST="https://judge.example.com/x")
    worker = compose(**REMOTE, **LIVE, JUDGE_PROVIDER_ALLOWLIST="judge.example.com")
    assert worker.wiring.provider.base_url == "https://judge.example.com"


def test_ap08_role__the_gold_set_is_a_reviewed_reference_set_or_nothing_starts(tmp_path):
    """Failure oracle: a role that starts on a missing or malformed reference set and
    publishes a calibration from it."""
    with pytest.raises(RuntimeMisconfigured):
        compose(JUDGE_GOLD_SET=str(tmp_path / "absent.json"))
    bad = tmp_path / "bad.json"
    bad.write_text('{"labels": []}')
    with pytest.raises(RuntimeMisconfigured):
        compose(JUDGE_GOLD_SET=str(bad))
