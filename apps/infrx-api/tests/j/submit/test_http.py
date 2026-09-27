#!/usr/bin/env python3
"""J2 egress: `HttpJudgeProvider` reaches the local judge fake (j2's 57512) and nothing else,
never retries, and turns a lost answer into `ambiguous` that reconciliation resolves.

    uv run --frozen pytest -q tests/j/submit/test_http.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from infrx.contracts import errors
from infrx.judge.submit import HttpJudgeProvider, collect, reconcile

from tests.j import fakes as j1

from . import fakes
from .judge_fake import JudgeFake
from .test_submit import Case


@pytest.fixture
def fake():
    server = JudgeFake()
    yield server
    server.close()


def over_http(fake, timeout_s: float = 5.0) -> Case:
    case = Case()
    case.provider = HttpJudgeProvider(fake.url, timeout_s=timeout_s)
    case.wiring = dataclasses.replace(case.wiring, provider=case.provider)
    return case


@pytest.mark.parametrize("url", ["https://api.anthropic.com", "http://10.0.0.7:57512",
                                 "http://judge.example:57512", "https://127.0.0.1:57512"])
def test_j2_http__egress_is_refused_to_anything_but_the_local_fake(url):
    with pytest.raises(errors.InvalidRequest):
        HttpJudgeProvider(url)


def test_j2_http__submit_and_collect_round_trip_through_the_fake(fake, monkeypatch):
    """An HTTP(S)_PROXY in the environment is not followed: the batch still lands here."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    case = over_http(fake)
    run = asyncio.run(asyncio.wait_for(case.submit_async(), 10))
    assert run.state == "submitted" and run.external_id == "batch-1"
    [post] = fake.posts
    assert post["submit_key"] == run.submit_key and len(post["items"]) == 3
    fake.outputs["batch-1"] = [[sid, json.dumps(j1.result())] for sid in case.ids]
    run = asyncio.run(collect(run.run_id, wiring=case.wiring))
    assert run.state == "completed" and len(case.ledger.results) == 3


def test_j2_http__a_rejection_releases_the_hold(fake):
    fake.mode = "reject"
    case = over_http(fake)
    assert asyncio.run(case.submit_async()).state == "failed"
    assert len(fake.posts) == 1


def test_j2_http__a_lost_answer_is_ambiguous_one_request_and_reconciled(fake):
    fake.mode, fake.delay_s = "slow", 1.0
    case = over_http(fake, timeout_s=0.2)
    run = asyncio.run(case.submit_async())
    assert run.state == "ambiguous" and len(fake.posts) == 1, "never retried"
    run = asyncio.run(reconcile(run.run_id, wiring=case.wiring))
    assert run.state == "submitted" and run.external_id == "batch-1"
    assert len(fake.posts) == 1


def test_j2_http__a_5xx_is_ambiguous_even_when_it_carries_an_id(fake):
    fake.mode = "error"
    case = over_http(fake)
    assert asyncio.run(case.submit_async()).state == "ambiguous"
    assert len(fake.posts) == 1


def test_j2_http__a_request_the_provider_never_took_is_released_by_reconciliation(fake):
    fake.mode, fake.delay_s = "drop", 1.0
    case = over_http(fake, timeout_s=0.2)
    run = asyncio.run(case.submit_async())
    assert run.state == "ambiguous"
    run = asyncio.run(reconcile(run.run_id, wiring=case.wiring))
    assert run.state == "failed" and case.ledger.committed(case.payer).is_zero
    assert len(fake.posts) == 1
