#!/usr/bin/env python3
"""WR-C6-LIVE (R244): `0054_lab_release_live.sql` on real PostgreSQL, and its SQL mutation list
(R32/R40: needs Docker, skips visibly without it; a survivor fails the suite and every check is
named by a mutant).

`lab_release_live {policy_ref}` is R2's `Live` for one policy revision, one row per arm, read
from R1's recorded assignments (`lab_rollout_assignments`) and the admitted jobs they name
(`infrx.jobs`): requests and errors (failed/expired) of terminal jobs, p99 of terminal -
admitted, spend in the unit the jobs settled in (two units refused, never converted),
quality coverage (requests with an operator or customer T2F feedback row), the candidate's L3
deployment revision `ready`, and `observed_until` = the database clock at read.

World: test_d9_rollout's (test_d7_lab_data's, with the operator seed: the dev deployment
`ready_private`, the prod one `active`). Job, ledger and feedback rows are fixtures written
with `session_replication_role = replica` (tests/content/world_pg.py's pattern): the read is
under test, not the admission or settlement paths that write them.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/d/test_code_mutants_live.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import pytest
from infrx.contracts import errors
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_rollout import PgReleaseStore

from . import checks_credit as cc
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_d9_rollout as d9

_reason = pgharness.unavailable()
DB = f"{pgharness.DATABASE}_live"
DB_MUT = f"{pgharness.DATABASE}_livemut"
FILE = "0054_lab_release_live.sql"
NEMO, uid, ok, t, seed = d9.NEMO, d9.uid, d9.ok, d9.t, d9.seed
TERMINAL_CAUSE = {"succeeded": "completed", "failed": "engine_error", "cancelled":
                  "client_cancelled", "expired": "queue_wait_expired"}


# ----------------------------------------------------------------------------- the world
def ref_of(conn, deployment: str) -> str:
    return conn.execute("select infrx.lab_serving_ref(%s)", (deployment,)).fetchone()[0]


def launch(conn, tag: int, candidate: str) -> tuple[str, dict]:
    """A NEMO canary revision on its own endpoint, `candidate` its one candidate, started."""
    body = {**d9.policy(uid(tag, 0xb0), weights=(5_000,), endpoint=uid(tag, 0xe0)),
            "candidates": [{"serving_ref": candidate, "weight_bp": 5_000}]}
    ref = t.publish(conn, body)
    ok(conn, "lab_release_start", {"provider_org_id": NEMO, "policy_ref": ref,
                                   "plan_digest": "sha256:" + "a1" * 32, "decided_by": d9.USER,
                                   "reason": "canary"})
    return ref, body


def job(conn, ref: str, body: dict, rid: str, serving: str, state: str, *, ms: int = 10,
        charged: str | None = None, feedback: tuple[str, ...] = (),
        regime: str = "credit") -> None:
    """One admitted job assigned to `serving` under revision `ref`: terminal `ms` after its
    admission (5 s after it was created) unless `queued`; `charged` its settled CREDIT debit (or USD `debit` on a
    legacy_usd job); a T2F feedback row per author role in `feedback`."""
    org = cc.personal_org(conn, cc.CONSUMER_1)
    with conn.transaction():
        conn.execute("set local session_replication_role = replica")
        conn.execute(cc.credit_job(rid, f"live-{rid}", org, cc.PROVIDER_WALLET))
        if regime == "legacy_usd":
            conn.execute("update infrx.jobs set accounting_regime = 'legacy_usd', price_version "
                         "= 'pv', price_snapshot = '{}', wallet_id = null, model_id = null, "
                         "requested_model = null, deployment_revision_id = null, "
                         "serving_version_id = null, rate_card_version = null, "
                         "policy_version = null where request_id = %s", (rid,))
        if state != "queued":
            conn.execute(
                "update infrx.jobs set state = %s, outcome_cause = %s, settlement_state = "
                "'settled', usage_certainty = 'authoritative', result_ref = 'r', admitted_at = admitted_at + interval '5 seconds', settled_at = "
                "admitted_at + interval '5 seconds' + make_interval(secs => %s / 1000.0), "
                "debit = %s where request_id = %s",
                (state, TERMINAL_CAUSE[state], ms,
                 charged if regime == "legacy_usd" and charged else 0, rid))
        if charged and regime == "credit":
            conn.execute("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
                         "operation_id, request_id, actor) values (%s, 'consumer', "
                         "'inference_debit', -%s::numeric, gen_random_uuid(), %s, 'settle')",
                         (cc.PROVIDER_WALLET, charged, rid))
        for n, role in enumerate(feedback):
            conn.execute("insert into infrx.feedback (feedback_id, org_id, request_id, "
                         "author_principal, author_role, channel, name, value_bool, "
                         "by_operator) values (%s, %s, %s, 'someone', %s, 'api', 'thumb', true, "
                         "%s)", (f"fb-{rid}-{n}", org, rid, role, role == "operator"))
        conn.execute(
            "insert into infrx.lab_rollout_assignments (policy_id, request_id, policy_ref, "
            "cohort_digest, serving_ref, pinned_by) values (%s, %s, %s, %s, %s, 'cohort')",
            (body["policy_id"], rid, ref, "sha256:" + "c" * 64, serving))


def live(conn, ref: str):
    return ok(conn, "lab_release_live", {"policy_ref": ref})


def store(conn) -> PgReleaseStore:
    return PgReleaseStore(connector(pgharness.dsn(conn.info.dbname)))


# ----------------------------------------------------------------------------- checks
@d9.rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session executes 0054's read; the platform role does."""
    probe = "select infrx.lab_release_live('{}'::jsonb)"
    reached = [s for s in cc.BROWSER if not (cc.refused_as(conn, s, probe) or "")
               .startswith("42501")]
    assert not reached, f"browser sessions reached lab_release_live: {reached}"
    got = cc.refused_as(conn, "service", "select infrx.lab_release_live("
                        f"'{{\"policy_ref\": \"{d9.BASE}\"}}')")
    assert (got or "").startswith("P0001"), f"the platform role cannot reach the read: {got}"
    return f"{len(cc.BROWSER)} browser sessions refused; the platform role reads"


def check_live_is_read_per_arm_from_the_assigned_terminal_jobs(conn) -> str:
    """R244: one row per arm of THIS revision's assignments: requests = terminal jobs (a
    queued one is not yet a request), errors = failed + expired (a cancel is not an error),
    p99 = percentile_disc(0.99) of terminal - admitted in ms (not the max), spend = the settled
    CREDIT debits, quality = requests with an operator or customer feedback row (a judge's is
    not), `observed_until` = the database clock. Commits (tags 0x41-0x42)."""
    candidate = ref_of(conn, cc.DEV_DEPLOYMENT)                        # ready_private
    ref, body = launch(conn, 0x41, candidate)
    other, other_body = launch(conn, 0x42, candidate)
    base = body["baseline_ref"]
    for i in range(1, 101):                                             # 100 terminal requests
        state = "failed" if i <= 3 else "expired" if i == 4 else \
            "cancelled" if i == 5 else "succeeded"
        job(conn, ref, body, uid(i, 0x41c), candidate, state, ms=i,
            charged={6: "1.50000000", 7: "2.25000000"}.get(i),
            feedback={6: ("customer", "customer"), 7: ("operator",),
                      8: ("judge",)}.get(i, ()))
    job(conn, ref, body, uid(101, 0x41c), candidate, "queued")
    job(conn, ref, body, uid(1, 0x41b), base, "succeeded", ms=40, charged="1.00000000",
        feedback=("customer",))
    job(conn, ref, body, uid(2, 0x41b), base, "cancelled", ms=7)
    job(conn, other, other_body, uid(1, 0x42c), candidate, "failed", ms=900)
    now = conn.execute("select infrx.now()").fetchone()[0]
    rows = live(conn, ref)
    assert [r["arm"] for r in rows] == ["baseline", "candidate"], rows
    b, c = rows
    assert (c["requests"], c["errors"], c["p99_ms"]) == (100, 4, 99), c
    assert (b["requests"], b["errors"], b["p99_ms"]) == (2, 0, 40), b
    assert c["spent"] == {"unit": "CREDIT", "value": "3.75000000"}, c
    assert b["spent"] == {"unit": "CREDIT", "value": "1.00000000"}, b
    assert (c["quality_covered"], b["quality_covered"]) == (2, 1), rows
    assert c["candidate_healthy"] is True and b["candidate_healthy"] is True, rows
    assert datetime.fromisoformat(c["observed_until"]) == now == \
        datetime.fromisoformat(b["observed_until"]), (rows, now)
    assert live(conn, other)[1]["requests"] == 1, "another revision's jobs leaked"
    return "per arm: 100/4/p99 99 ms, 3.75 CREDIT, 2 covered; baseline 2/0/40; the store reads it"


def check_a_revision_without_assigned_jobs_is_empty(conn) -> str:
    """R244: no assignment naming an admitted job yet is no observation at all - an empty
    read (the store's None; the pass holds), never two arms of zeros; an unknown revision is
    `not_found`. Commits (tag 0x43)."""
    ref, _ = launch(conn, 0x43, ref_of(conn, cc.DEV_DEPLOYMENT))
    assert live(conn, ref) == [], live(conn, ref)
    unknown = d9.BASE.replace("serving", "policy")
    assert d9.refusal(conn, "lab_release_live", {"policy_ref": unknown}) == "not_found"
    return "empty for no assigned job; unknown revision not_found"


def check_units_never_mix(conn) -> str:
    """R244 / LANE-RULES 8: spend is in the unit the jobs settled in. A revision whose jobs
    settled in CREDIT and USD is refused by name, never summed; legacy USD alone reads as USD,
    which the store refuses as no Lab unit (never converted). Commits (tags 0x44-0x45)."""
    candidate = ref_of(conn, cc.DEV_DEPLOYMENT)
    mixed, body = launch(conn, 0x44, candidate)
    job(conn, mixed, body, uid(1, 0x44c), candidate, "succeeded", charged="1.00000000")
    job(conn, mixed, body, uid(2, 0x44c), candidate, "succeeded", charged="2.00000000",
        regime="legacy_usd")
    assert d9.refusal(conn, "lab_release_live", {"policy_ref": mixed}) == "invalid_request"
    usd, ubody = launch(conn, 0x45, candidate)
    job(conn, usd, ubody, uid(1, 0x45c), candidate, "succeeded", charged="2.00000000",
        regime="legacy_usd")
    assert [r["spent"] for r in live(conn, usd)] == [
        {"unit": "USD", "value": "0.00000000"}, {"unit": "USD", "value": "2.00000000"}]
    return "CREDIT + USD refused; USD alone read as USD and refused by the store"


def check_the_candidate_is_healthy_only_on_a_ready_deployment(conn) -> str:
    """R244: health = the candidate's L3 deployment revision is `ready` (ready_private or
    active) - a retired one, or a ref naming no deployment, is unhealthy. Commits (tags
    0x46-0x48)."""
    active = ref_of(conn, cc.PUBLIC_DEPLOYMENT)
    retired_id = uid(1, 0x46d)
    conn.execute(
        "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
        "provider_org_id, environment, serving_version_id, visibility, state, max_input_tokens,"
        " max_output_tokens, created_by) values (%s, %s, %s, 'dev', %s, 'private', 'retired', "
        "30720, 2048, 'live-test')", (retired_id, cc.DEV_ENDPOINT, NEMO, cc.SERVING))
    seen = {}
    for tag, candidate in ((0x46, active), (0x47, ref_of(conn, retired_id)), (0x48, d9.CAND)):
        ref, body = launch(conn, tag, candidate)
        job(conn, ref, body, uid(1, tag << 4), candidate, "succeeded")
        seen[tag] = [r["candidate_healthy"] for r in live(conn, ref)]
    assert seen == {0x46: [True, True], 0x47: [False, False], 0x48: [False, False]}, seen
    return "active healthy; retired and unknown deployments unhealthy"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_live_is_read_per_arm_from_the_assigned_terminal_jobs,
    check_a_revision_without_assigned_jobs_is_empty,
    check_units_never_mix,
    check_the_candidate_is_healthy_only_on_a_ready_deployment)}
ROLES, PER_ARM, EMPTY, UNITS, HEALTH = CHECKS


# ----------------------------------------------------------------------------- mutants
def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("live_browser_reads", "$$;\n", "$$;\ngrant usage on schema infrx to authenticated;\n"
       "grant execute on function infrx.lab_release_live(jsonb) to authenticated;\n", ROLES,
       "a browser session reads every release's traffic, spend and health"),
    _s("live_any_revision", "       where a.policy_ref = p_args->>'policy_ref'\n         and",
       "       where", PER_ARM, "another revision's requests and errors decide this release"),
    _s("live_units_of_any_revision", "                    where a.policy_ref = p_args->>"
       "'policy_ref');", "                   );", UNITS,
       "another revision's jobs decide this one's unit (a legacy job elsewhere refuses it)"),
    _s("live_queued_is_a_request",
       "         and j.state in ('succeeded', 'failed', 'cancelled', 'expired'))",
       "         and true)", PER_ARM, "a request still queued counts as a success"),
    _s("live_cancel_is_an_error", "(where t.state in ('failed', 'expired'))",
       "(where t.state in ('failed', 'expired', 'cancelled'))", PER_ARM,
       "a user's cancel rolls the candidate back"),
    _s("live_expired_not_an_error", "(where t.state in ('failed', 'expired'))",
       "(where t.state in ('failed'))", PER_ARM, "an expired request hides the breach"),
    _s("live_p99_is_the_max", "percentile_disc(0.99)\n", "percentile_disc(1.0)\n", PER_ARM, "one outlier reads as the p99 and rolls a healthy candidate back"),
    _s("live_p99_from_queue", "j.settled_at - j.admitted_at", "j.settled_at - j.created_at",
       PER_ARM, "latency measured from another instant than admission"),
    _s("live_spend_is_the_v1_debit", "when 'credit' then coalesce((select -l.amount",
       "when 'credit' then coalesce((select 0 * l.amount", PER_ARM,
       "a CREDIT job's spend reads as its zero v1 debit (R87): the budget never trips"),
    _s("live_judge_covers", "f.author_role in ('customer', 'operator')",
       "f.author_role in ('customer', 'operator', 'judge')", PER_ARM,
       "a judge's own score counts as human quality coverage"),
    _s("live_operator_not_covering", "f.author_role in ('customer', 'operator')",
       "f.author_role in ('customer')", PER_ARM, "an operator's label is not quality coverage"),
    _s("live_baseline_is_candidate",
       "case when a.serving_ref = v_doc->>'baseline_ref' then 'baseline' else 'candidate' end",
       "'candidate'", PER_ARM, "the baseline's traffic is judged as the candidate's"),
    _s("live_clock_is_wall", "'observed_until', infrx.now()", "'observed_until', now()",
       PER_ARM, "observed_until is not the database clock the jobs are stamped with"),
    _s("live_empty_as_zeros", "  if cardinality(v_units) = 0 then\n    return '[]';\n  end if;\n",
       "", EMPTY, "no observation reads as two arms of zeros"),
    _s("live_unknown_revision_empty", "  if v_doc is null then\n    perform infrx.refuse("
       "'not_found'", "  if false then\n    perform infrx.refuse('not_found'", EMPTY,
       "a misspelt revision reads as quiet traffic"),
    _s("live_units_mix", "  if cardinality(v_units) > 1 then\n", "  if false then\n", UNITS,
       "CREDIT and USD are summed into one spend"),
    _s("live_usd_as_credit", "case v_units[1] when 'credit' then 'CREDIT' else 'USD' end",
       "'CREDIT'", UNITS, "legacy USD spend is relabelled CREDIT"),
    _s("live_retired_is_healthy", "coalesce(d.state in ('ready_private', 'active'), false)",
       "coalesce(d.state is not null, false)", HEALTH,
       "a retired candidate deployment reads as healthy"),
    _s("live_unknown_deployment_healthy",
       "coalesce(d.state in ('ready_private', 'active'), false)",
       "coalesce(d.state in ('ready_private', 'active'), true)", HEALTH,
       "a candidate naming no deployment reads as healthy"),
    _s("live_active_unhealthy", "d.state in ('ready_private', 'active')",
       "d.state in ('ready_private')", HEALTH, "a published candidate reads as unhealthy"),
)


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("name", list(CHECKS))
def test_release_live(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def test_the_list_is_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)
    assert not _d.superseded(SQL_MUTANTS)


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(CHECKS) - {m.check for m in SQL_MUTANTS})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = d7.kill(mutant, DB_MUT, sys.modules[__name__])
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
