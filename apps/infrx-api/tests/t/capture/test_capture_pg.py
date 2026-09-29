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


def policy(capture, w, org, key, connect=None):
    source = capture.ConsentSource(connect or w.connect(), ttl_s=0.0)
    return asyncio.run(source.policy(SimpleNamespace(org_id=org, key_id=key), NOW))


def runtime_login(w):
    """0021's dedicated gateway login (`infrx_runtime`), stood in for by `set role` on the
    harness's connection: its privileges, not the platform role's."""
    async def connect():
        import psycopg
        conn = await psycopg.AsyncConnection.connect(w.dsn, autocommit=True,
                                                     prepare_threshold=None)
        await conn.execute("set role infrx_runtime")
        return conn
    return connect


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


def check_the_runtime_login_reads_consent_through_the_rpc(capture, w) -> None:
    """WR-LC-HOSTED: the dedicated login (no read of `api_keys` or `consent_history`) gets the
    consented policy through 0057's `trace_consent`. Oracle: a direct table read - capture
    silently off on that login."""
    version = consent(w, b.ORG_A, "full")
    opt_in(w, b.KEY_A, "full")
    got = policy(capture, w, b.ORG_A, b.KEY_A, runtime_login(w))
    assert (got.trace_mode, got.consent_version) == (TraceMode.full, version)


def check_without_the_grant_the_runtime_login_reads_off(capture, w) -> None:
    """Until 0057 reaches a database (hosted: its window), the runtime login has no read: the
    source fails closed to off and never raises into the request."""
    consent(w, b.ORG_A, "full")
    opt_in(w, b.KEY_A, "full")
    w.owner.execute("revoke execute on function infrx.trace_consent(uuid, uuid) "
                    "from infrx_runtime")
    try:
        got = policy(capture, w, b.ORG_A, b.KEY_A, runtime_login(w))
    except Exception as raised:                     # noqa: BLE001 - the defect under test
        raise AssertionError(f"the refused read raised: {raised!r}") from None
    assert got == capture.off_mode_policy(b.ORG_A, NOW)
    assert policy(capture, w, b.ORG_A, b.KEY_A).trace_mode is TraceMode.full   # the pool reads


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

