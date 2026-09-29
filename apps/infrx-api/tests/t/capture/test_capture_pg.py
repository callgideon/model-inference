"""WR-C6-CAPTURE (a) on real PostgreSQL: `ConsentSource` over every migration, as the gateway's
pool reads it (`service_role`), on the lane's task-local database.

    INFRX_D_TASK=t2f uv run --frozen pytest -q tests/t/capture/test_capture_pg.py

Each `check_*(module, world)` takes `infrx.gateway.capture` as an argument, so
`tests/t/capture/mutants.py` kills its SQL in a copy on a fresh world per run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from infrx.contracts.conformance import builders as b
from infrx.contracts.records import TraceMode
from infrx.gateway import capture as module

from ...g.ops import pgworld

pytestmark = pgworld.needs_pg
NOW = datetime.now(timezone.utc)


def consent(w, org: str, mode: str, *, revoked: bool = False) -> int:
    """A new consent head for `org` (0003's immutable history: a new version, or a revocation
    of the head)."""
    version = w.one("select coalesce(max(consent_version), 0) + 1 from infrx.consent_history "
                    "where org_id = %s", (org,))
    w.owner.execute("insert into infrx.consent_history (org_id, consent_version, trace_mode, "
                    "content_retention_days, evaluation_consent, actor_principal, effective_at)"
                    " values (%s, %s, %s, 30, %s, 'owner@t2f', infrx.now() - interval '1 hour')",
                    (org, version, mode, mode == "full"))
    if revoked:
        w.owner.execute("update infrx.consent_history set revoked_at = infrx.now() "
                        "where org_id = %s and consent_version = %s", (org, version))
    return version


def opt_in(w, key: str, mode: str | None) -> None:
    w.owner.execute("update public.api_keys set trace_mode = %s where id = %s", (mode, key))


def policy(capture, w, org, key):
    source = capture.ConsentSource(w.connect(), ttl_s=0.0)
    return asyncio.run(source.policy(SimpleNamespace(org_id=org, key_id=key), NOW))


def check_the_keys_opt_in_under_its_orgs_consent_head(capture, w) -> None:
    """Oracle: the key or the org side not read (either one alone decides), a head that is
    not the newest version, or a key of another organization answering for this one."""
    version = consent(w, b.ORG_A, "full")
    opt_in(w, b.KEY_A, "full")
    got = policy(capture, w, b.ORG_A, b.KEY_A)
    assert (got.trace_mode, got.consent_version) == (TraceMode.full, version)
    opt_in(w, b.KEY_A, "minimal")
    assert policy(capture, w, b.ORG_A, b.KEY_A).trace_mode is TraceMode.minimal
    opt_in(w, b.KEY_A, None)
    assert policy(capture, w, b.ORG_A, b.KEY_A).trace_mode is TraceMode.off
    opt_in(w, b.KEY_A, "full")
    consent(w, b.ORG_A, "minimal")
    assert policy(capture, w, b.ORG_A, b.KEY_A).trace_mode is TraceMode.minimal
    consent(w, b.ORG_B, "full")
    opt_in(w, b.KEY_B, "full")                        # ORG_B's key, named under ORG_A
    assert policy(capture, w, b.ORG_A, b.KEY_B).trace_mode is TraceMode.off


def check_a_revoked_head_is_off_not_an_older_consent(capture, w) -> None:
    """Oracle: the head chosen among unrevoked rows only - revoking the current consent
    would silently fall back to the previous one."""
    consent(w, b.ORG_A, "full")
    consent(w, b.ORG_A, "full", revoked=True)
    opt_in(w, b.KEY_A, "full")
    assert policy(capture, w, b.ORG_A, b.KEY_A).trace_mode is TraceMode.off


CHECKS = {name: check for name, check in dict(globals()).items() if name.startswith("check_")}


@pytest.fixture
def world():
    w = pgworld.world("capture")
    try:
        yield w
    finally:
        w.owner.close()


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_consent_source_on_postgresql(name, world):
    CHECKS[name](module, world)

