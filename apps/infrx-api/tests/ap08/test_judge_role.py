"""AP-08 (api-judge-2): the judge role's composition (`python -m infrx.lab.workers judge`'s
`compose`) - the START job exists only with AP-07's eligible read composed, egress beyond
loopback only to an https host the operator allowlisted AND only in live mode (P-10), and the
gold set is a reviewed reference set or the role refuses to start. No database is reached
(the connector is lazy); T3's retention is replaced as `tests/j/calibration`'s composition
test replaces it. The process itself is `test_judge_cli_pg.py`.
"""
from __future__ import annotations

import asyncio
import json

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
    """Failure oracle: no start job (queued runs never start), or one over no trace read or
    another than AP-07's over the role's own retention (it would judge nothing, or guess)."""
    from infrx.traces.eligible import TraceEligible
    seen, steps = [], {}

    async def start_pass(queued, wiring, eligible):
        seen.append(eligible)
        return {}

    def every(interval, step, what):
        steps[what] = step
        return asyncio.sleep(0)

    monkeypatch.setattr(start, "start_pass", start_pass)
    monkeypatch.setattr(lab_workers, "every", every)
    worker = compose()
    assert set(worker.tasks) == {"judge_sweep", "judge_collect", "judge_start"}
    assert worker.wiring.rubric_of is not None, "runs graded with the first rubric"
    asyncio.run(worker.tasks["judge_start"]())
    asyncio.run(steps["judge start"]())
    assert isinstance(seen[0], TraceEligible) and seen[0].retention is worker.wiring.retention


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


def test_ap08_role__each_collect_pass_is_followed_by_the_gold_set_calibration(
        monkeypatch, tmp_path):
    """Failure oracle: a configured gold set never graded (the Lab keeps `uncalibrated`), or
    a calibration published before the pass collected."""
    from infrx.judge.calibration import goldset
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({
        "provider_org_id": "b0000001-0000-4000-8000-000000000001",
        "org_id": "0a000000-0000-4000-8000-0000000000a1", "judge_model": "judge-1",
        "rubric_version": 1, "reviewed_by": "operator@infrx.test", "review_ref": "ap8",
        "labels": [{"sample_id": "5a000000-0000-4000-8000-000000000001",
                    "verdict": "correct"}]}))
    order: list[str] = []
    steps = {}

    async def judge_pass(wiring, providers):
        order.append("collect")
        return {}

    async def calibrate(ledger, results_of, g):
        order.append(f"calibrate {g.judge_model}")

    def every(interval, step, what):
        steps[what] = step
        return asyncio.sleep(0)

    monkeypatch.setattr(lab_workers, "judge_pass", judge_pass)
    monkeypatch.setattr(goldset, "calibrate", calibrate)
    monkeypatch.setattr(lab_workers, "every", every)
    worker = compose(JUDGE_GOLD_SET=str(gold))
    asyncio.run(worker.tasks["judge_collect"]())
    asyncio.run(steps["judge collect"]())
    assert order == ["collect", "calibrate judge-1"]
