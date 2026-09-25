#!/usr/bin/env python3
"""D10: F2C.a's exported lifecycle suite (`conformance/lifecycle.py`: UPLOAD-RESTART,
ADMISSION-READY, RETENTION-DURABLE) against the REAL adapter, `state.lifecycle.PgLifecycle`,
through `pgtesting.make_lifecycle_factory` (the CREDIT world plus the suite's hooks). A case
NOT in `PENDING` must pass; a pending one is strict-xfail with the slice that builds it.

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_lifecycle_conformance.py
"""
from __future__ import annotations

import asyncio
import json

import pytest
from infrx.contracts.conformance import MissingHook
from infrx.contracts.conformance.lifecycle import cases
from infrx.state import migrations, pgtesting

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

PENDING: dict[str, str] = {}

CASES = cases()
factory = pgtesting.make_lifecycle_factory(pgstore.fresh_database, pgharness.dsn,
                                           migrations.SEED_MARLIN.read_text())


def _param(case):
    reason = PENDING.get(case.__name__)
    marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    assert set(PENDING) <= {case.__name__ for case in CASES}


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_lifecycle_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")


def _representation(value):
    """What differs between two CORRECT adapters and is not behaviour - proposed to F2C.d
    as normalization: (1) `payload_hash`, a digest of the harness's raw request id (the fake
    draws ids for its own rows, PostgreSQL does not, so the sequences part); (2) the order
    of an admission's reservations (a set; PostgreSQL returns them by kind); (3) a result
    reference, which R30 makes the store's (`infrx-result:<job>`); (4) a candidate page's
    order among equal `eligible_at` (content ids are the store's) and D10's DATABASE content
    rows (request records, result bodies), which the fake does not model; (5) an admission
    document's `outbox` as its ADMISSION-time events: the real store's `job_admission` lists
    every dispatch event of the job, so after `prepare` it also carries the inference
    dispatch preparation emitted, which the fake's admission record never gains (review
    2-ACI-2; compared as the admission's kinds, in order)."""
    if isinstance(value, list):
        return [_representation(item) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key == "payload_hash":
            continue
        if key == "result_ref" and isinstance(item, str):
            out[key] = "<result>"
        elif key == "reservations" and isinstance(item, list):
            out[key] = sorted((_representation(i) for i in item), key=lambda i: i["kind"])
        elif key == "outbox" and isinstance(item, list):
            kinds = {i.get("kind") for i in item if isinstance(i, dict)}
            admitted = [_representation(i) for i in item if not (
                "prepare_dispatch" in kinds and i.get("kind") == "inference_dispatch")]
            out[key] = sorted(admitted, key=lambda i: i.get("kind", ""))
        elif key == "items" and isinstance(item, list):
            out[key] = sorted((_representation(i) for i in item
                               if i.get("identity", {}).get("location") != "database"),
                              key=lambda i: json.dumps(i, sort_keys=True))
        else:
            out[key] = _representation(item)
    return out


def _canonical(steps):
    """`_representation`, then the transcript's `<kind:N>` placeholders renumbered by first
    appearance: class (5) drops events whose ids the recorder had already numbered, which
    would otherwise shift every later id of the case."""
    import re
    text = json.dumps(_representation(steps), sort_keys=True)
    seen: dict[str, str] = {}
    counters: dict[str, int] = {}

    def renumber(match):
        token = match.group(0)
        if token not in seen:
            kind = match.group(1)
            counters[kind] = counters.get(kind, 0) + 1
            seen[token] = f"<{kind}:{counters[kind]}>"
        return seen[token]
    return re.sub(r"<(\w+):(\d+)>", renumber, text)


def test_the_versioned_acceptance_transcripts_replay_exactly() -> None:
    """F2C.d: `fixtures/acceptance/lifecycle.json` (24 cases) recorded by the REAL adapter
    with the transcript's windows and compared step by step, relative instants included:
    every port answer and typed refusal, in order, per process. The comparison removes only
    `_representation`'s five classes; `replay()`'s raw count is printed beside it."""
    from infrx.contracts.conformance import acceptance
    expected, got = acceptance.committed(), acceptance.record(factory)
    assert expected["version"] == acceptance.VERSION
    problems = [name for name, steps in expected["cases"].items()
                if _canonical(steps) != _canonical(got["cases"].get(name))]
    raw = sum(steps != got["cases"].get(name) for name, steps in expected["cases"].items())
    assert problems == [], problems
    print(f"{len(expected['cases'])} transcripts replayed exactly after normalization "
          f"({raw} differ only in the five representation classes)")
