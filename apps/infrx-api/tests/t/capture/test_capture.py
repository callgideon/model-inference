"""WR-C6-CAPTURE (lab-capture): trace capture behind `TRACE_PUMPS`, on fakes and a real spool.

    uv run --frozen pytest -q tests/t/capture

(a) the consent source: a key's own opt-in (`api_keys.trace_mode`, null = off) under its
organization's consent head (`infrx.consent_history`), read at request time, cached per
process for a bounded TTL, fail closed; the ingress hands admission that policy instead of
`off_mode_policy` only when the capture is composed.

Each case names the defect it catches (its oracle); `mutants.py` kills each decision.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from infrx.contracts.records import ConsentSnapshot, TraceMode
from infrx.gateway import capture

from ...g import support

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=1)
AUTH = SimpleNamespace(org_id=support.ORG, key_id=support.KEY)


def run(coroutine):
    return asyncio.run(coroutine)


def row(key="full", org="full", *, version=2, days=30, evaluation=True, effective=EARLIER,
        revoked=None):
    """What `CONSENT_SQL` answers: the key's mode, then the org's consent head (all None
    when the org has no consent row)."""
    if org is None:
        return (key, None, None, None, None, None, None)
    return (key, version, org, days, evaluation, effective, revoked)


class Reads:
    """A consent reader that answers `rows` in turn and counts the reads."""

    def __init__(self, *rows, fail=False) -> None:
        self.rows, self.fail, self.calls = list(rows), fail, []

    async def __call__(self, org_id, key_id):
        self.calls.append((org_id, key_id))
        if self.fail:
            raise OSError("postgres did not answer")
        return self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]


def source(reads, clock=lambda: 0.0, **kw):
    consent = capture.ConsentSource(None, clock=clock, **kw)
    consent.read = reads
    return consent


# --- (a) the consent source -----------------------------------------------------------
def test_a_consented_key_under_a_consenting_org_captures_at_the_lower_of_the_two():
    """Oracle: the key's opt-in ignored (the org's mode wins), the org's consent ignored
    (the key's wins), or `max` for `min` - each raises capture above what one side
    consented to."""
    cases = {("full", "full"): TraceMode.full, ("minimal", "full"): TraceMode.minimal,
             ("full", "minimal"): TraceMode.minimal, ("off", "full"): TraceMode.off,
             ("full", "off"): TraceMode.off}
    for (key, org), want in cases.items():
        got = run(source(Reads(row(key, org))).policy(AUTH, NOW))
        assert got.trace_mode is want, (key, org, got.trace_mode)
        assert got.org_id == support.ORG


def test_a_key_that_never_opted_in_or_an_org_without_consent_is_off():
    """Oracle: a null key mode read as the org's (opt-out instead of opt-in), or a missing
    consent row read as consent."""
    for answer in (row(None, "full"), row("full", None), None):
        got = run(source(Reads(answer)).policy(AUTH, NOW))
        assert got == capture.off_mode_policy(support.ORG, NOW), answer


def test_a_revoked_or_not_yet_effective_consent_is_off():
    """Oracle: the revocation or the effective time ignored - capture under a consent that
    is not in force."""
    for answer in (row(revoked=NOW - timedelta(seconds=1)), row(effective=NOW + timedelta(1))):
        got = run(source(Reads(answer)).policy(AUTH, NOW))
        assert got.trace_mode is TraceMode.off, answer


def test_the_policy_carries_the_consent_head_and_evaluation_only_at_full():
    """Oracle: a snapshot that loses its version/retention (the job would pin consent 0),
    or evaluation consent kept after the key lowered the mode below full."""
    full = run(source(Reads(row(version=7, days=14))).policy(AUTH, NOW))
    assert (full.consent_version, full.content_retention_days, full.evaluation_consent,
            full.effective_at) == (7, 14, True, EARLIER)
    minimal = run(source(Reads(row("minimal", "full"))).policy(AUTH, NOW))
    assert minimal.evaluation_consent is False


def test_consent_is_read_once_per_key_within_the_ttl_and_again_after_it():
    """Oracle: no cache (a database read per request), a cache keyed by the org only (one
    key's opt-in answers for another), or no expiry (a revocation never lands)."""
    clock = [0.0]
    reads = Reads(row("full", "full"), row("full", "full"), row("full", "off"))
    consent = source(reads, clock=lambda: clock[0])
    other = SimpleNamespace(org_id=support.ORG, key_id=support.IDS.provider_dev_key)
    assert run(consent.policy(AUTH, NOW)).trace_mode is TraceMode.full
    assert run(consent.policy(AUTH, NOW)).trace_mode is TraceMode.full
    run(consent.policy(other, NOW))
    assert reads.calls == [(support.ORG, support.KEY), (support.ORG, other.key_id)]
    clock[0] = capture.CONSENT_TTL_S + 1
    assert run(consent.policy(AUTH, NOW)).trace_mode is TraceMode.off
    assert len(reads.calls) == 3


def test_the_consent_cache_is_bounded():
    """Oracle: an unbounded cache - key ids are the tenant's to mint."""
    consent = source(Reads(row()), max_entries=2)
    for n in range(5):
        run(consent.policy(SimpleNamespace(org_id=support.ORG, key_id=f"k{n}"), NOW))
    assert len(consent.cache) == 2


def test_a_consent_read_that_fails_is_off_and_never_raises():
    """Oracle: a failure that raises into the request (inference would depend on the trace
    path) or reads as consent."""
    got = run(source(Reads(fail=True)).policy(AUTH, NOW))
    assert got == capture.off_mode_policy(support.ORG, NOW)


def test_the_runtime_login_without_a_grant_reads_off():
    """0021's `infrx_runtime` has no read of `consent_history` or `api_keys` (a grant would
    be a migration): the source then fails closed to off, it does not raise. The failure is
    the one `pg_rows` maps every psycopg error to."""
    from infrx.contracts import errors

    async def refused(connect, sql, params):
        raise errors.DependencyUnavailable("postgres: InsufficientPrivilege")
    consent = capture.ConsentSource("runtime-login", clock=lambda: 0.0)
    consent.rows = refused
    assert run(consent.policy(AUTH, NOW)) == capture.off_mode_policy(support.ORG, NOW)


def test_the_sql_reads_the_key_of_its_own_org_and_the_consent_head():
    """Oracle (with the PostgreSQL case in test_capture_pg.py): the statement's binding in
    the wrong order, or the head chosen among unrevoked rows only (a revoked head would fall
    back to an older consent instead of off)."""
    calls = []

    async def rows(connect, sql, params):
        calls.append((sql, params))
        return [row()]
    consent = capture.ConsentSource("connect", clock=lambda: 0.0)
    consent.rows = rows
    assert run(consent.read(support.ORG, support.KEY)) == row()
    [(sql, params)] = calls
    assert params == (support.KEY, support.ORG)
    assert "order by h.consent_version desc limit 1" in sql and "revoked_at is null" not in \
        sql.split("left join")[1]


# --- (a) the ingress seam ---------------------------------------------------------------
class Policy:
    """A composed capture whose policy is `mode` for every request."""

    def __init__(self, mode: TraceMode) -> None:
        self.mode, self.asked = mode, []

    async def policy(self, auth, now) -> ConsentSnapshot:
        self.asked.append((auth.org_id, auth.key_id, now))
        return ConsentSnapshot(org_id=auth.org_id, consent_version=3, trace_mode=self.mode,
                               content_retention_days=30, evaluation_consent=False,
                               effective_at=now)

    def response(self, accepted, request, token):
        return accepted


def test_the_ingress_admits_with_the_composed_capture_policy():
    """Oracle: the ingress keeps `off_mode_policy` although a capture is composed (o01's
    NOT RUN: nothing a served request does reaches a spool), or asks for another tenant."""
    calls, accept = support.recorder()
    composed = Policy(TraceMode.full)
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept, capture=composed))
    answer = TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                  json={**support.BODY})
    assert answer.status_code == 202, answer.text
    policy = calls[0][1].trace_policy
    assert (policy.trace_mode, policy.consent_version) == (TraceMode.full, 3)
    assert composed.asked[0][:2] == (support.ORG, support.KEY)
    assert composed.asked[0][2] == calls[0][1].created_at


def test_without_a_composed_capture_the_ingress_policy_is_off():
    """Oracle: the switch-off path changed (E4's launched behaviour)."""
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    assert TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                json=support.BODY).status_code == 202
    assert calls[0][1].trace_policy.trace_mode is TraceMode.off
