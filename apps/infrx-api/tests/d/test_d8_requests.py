#!/usr/bin/env python3
"""The remaining LW3 requests to lab-sql (`0043_lab_reads_and_proposals.sql`) on real
PostgreSQL: WR-N4-2 (dataset list), WR-B4-2 (experiments, checkpoint listing, run timeline,
finished runs stay finished), WR-R4-2 (release proposals through D9's fence), SR-J3-1 (the
Lab's judge-runs door with J3's calibration), SR-R1-1 (the router's port over D9, runtime
only) and WR-I2L-4 (the control service's bounded login) - LAB-ACCESS, EVAL-COMPARE,
ROLLOUT-PIN, JUDGE-SCORES, OPS-RECOVER and DUR-RLS.

World: test_d8_ledgers' (test_d7_lab_data's role matrix and Lab data, the flag
`lab_submission`, NEMO's payer budget, C1's grant to NEMO also for external_judging) plus the
operator seed's registry (the alias `nemostation/marlin-2b`, its public deployment on the
prod endpoint and serving versions). Each `check_*` is the check a mutant in
`code_mutants_d8.py` (`REQUESTS`) must break.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d8_requests.py
"""
from __future__ import annotations

import json

import psycopg
import pytest
from infrx.contracts.lab import records
from infrx.state import migrations
from psycopg.types.json import Jsonb

from . import checks
from . import checks_admission as ca
from . import checks_credit as cc
from . import pgharness
from . import test_d7_lab_data as t
from . import test_d8_ledgers as d8
from . import test_d9_rollout as d9

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d8q"

l2, NEMO, OTHER, C1, BOTH = t.l2, t.NEMO, t.OTHER, t.C1, t.BOTH
DEV, ADMIN, VIEWER = l2.DEV, l2.ADMIN, l2.VIEWER
call, ok, refusal, rolled_back, uid = t.call, t.ok, t.refusal, t.rolled_back, t.uid
TABLES = ("lab_experiments", "lab_release_proposals", "lab_judge_calibrations")
RPCS = ("lab_list_datasets", "lab_put_experiment", "lab_experiments",
        "lab_checkpoint_listing", "lab_propose_release", "lab_decide_release_proposal",
        "lab_release_proposals", "lab_put_judge_calibration")
ALIAS = ca.ALIAS
PROTOCOL = {"confidence": 0.95, "margin": 0.0, "min_cases": 2}
PROTOCOL_DIGEST = "sha256:" + "b" * 64
UNCALIBRATED = {"state": "uncalibrated", "labels": 0, "required": 30, "agreement": None,
                "interval": None}
REQUEST = uid(1, 0x6e)
W: dict[str, str] = {}


def serving_ref(version_id: str) -> str:
    """A stand-in ref, keyed by `version_id` (whatever id the caller means for its case).
    `release_active` never resolves this: it is opaque to every D9 RPC (they store a
    candidate/baseline ref as a string, never dereference it), and the one reader that does
    resolve one (`release_active`, WR-E8L-2) is given `W['candidate_ref']` below instead -
    a real ref of an actual deployment, built by the one function that builds one
    (`infrx.lab_serving_ref`, 0045)."""
    return f"lab:serving:{NEMO}:{version_id}@sha256:{'e' * 64}"


def run_record(conn, tag: int) -> str:
    """A published NEMO eval run record (not created)."""
    ds = t.publish(conn, t.manifest(uid(1, tag), n=2, tag=tag))
    return t.publish(conn, t.eval_run(uid(2, tag), ds, t.publish(conn, t.harness(uid(3, tag)))))


def experiment(eid: str, base: str, cand: str, **over) -> dict:
    return {"provider_org_id": NEMO, "experiment_id": eid, "protocol": PROTOCOL,
            "protocol_digest": PROTOCOL_DIGEST, "baseline_run_ref": base,
            "candidate_run_ref": cand, "actor": "dev@nemo", **over}


def report(base: str, cand: str, digest: str = PROTOCOL_DIGEST) -> str:
    return records.canonical({"schema": "infrx.eval_report.1", "baseline_run": base,
                              "candidate_run": cand, "protocol_digest": digest,
                              "decision": {"outcome": "reject"}}).decode()


def policy(tag: int, *, endpoint: str = cc.PROD_ENDPOINT, weights=(2500,)) -> dict:
    """A NEMO canary on the alias's prod endpoint: baseline = the listed serving version,
    candidate = the second serving version of the model, deployed (`W['candidate_ref']`,
    WR-E8L-2's identity - L3's `operations.serving_ref` in the R191 form, a real
    `deployment_revision_id`, not a bare `serving_version_id`)."""
    body = {**d9.policy(uid(tag, 0xb0), endpoint=endpoint, weights=weights),
            "baseline_ref": serving_ref(cc.SERVING),
            "candidates": [{"serving_ref": W["candidate_ref"], "weight_bp": w}
                           for w in weights]}
    return body


def launch(conn, tag: int, **kw) -> tuple[str, dict]:
    body = policy(tag, **kw)
    ref = t.publish(conn, body)
    ok(conn, "lab_release_start", {"provider_org_id": NEMO, "policy_ref": ref,
                                   "plan_digest": "sha256:" + "a1" * 32, "decided_by": DEV,
                                   "reason": "canary"})
    return ref, body


def execute_all(conn, statements: str, params: dict) -> None:
    """`;`-separated statements, each with the named parameters it uses."""
    for statement in statements.split(";"):
        conn.execute(statement, params)


def as_runtime(conn, sql: str, params=()):
    """(SQLSTATE or None, rows) of `sql` as infrx_runtime; writes stay in the check."""
    try:
        with conn.transaction():
            pgharness.become(conn, "infrx_runtime")
            rows = conn.execute(sql, params).fetchall()
            conn.execute("reset role")
    except psycopg.Error as refused:
        return refused.sqlstate, str(refused).split(":", 1)[0]
    return None, rows


def active(conn, alias: str = ALIAS):
    return as_runtime(conn, "select record, policy_ref, revisions, shadow_limit from "
                            "infrx.release_active(%s)", (alias,))


def eligible(conn, policy_id: str, org: str) -> bool:
    return as_runtime(conn, "select infrx.release_eligible(%s, %s)", (policy_id, org))[1][0][0]


def assignment(ref: str, request: str, serving: str) -> dict:
    return {"schema": "lab.rollout_assignment.1", "provider_org_id": NEMO, "policy_ref": ref,
            "request_id": request, "cohort_digest": "sha256:" + "c" * 64,
            "serving_ref": serving, "pinned_by": "cohort"}


def door(conn, user: str | None, provider: str = NEMO, request: str = REQUEST):
    try:
        with conn.transaction():
            conn.execute(checks._jwt(user) if user else checks.SESSIONS["anon"])
            answer = conn.execute("select public.lab_judge_runs(%s::uuid, %s::uuid)",
                                  (provider, request)).fetchone()[0]
            conn.execute("reset role")
    except psycopg.Error as refused:
        return refused.sqlstate, None
    return None, answer


def judged(conn, run: str, *, sent=(REQUEST,), result: dict | None = None,
           actual: str | None = None) -> None:
    """A Lab-requested judge run of NEMO over REQUEST (and one more), sent, submitted, with
    `result` recorded for REQUEST and settled at `actual` when given."""
    state, cfg = d8_door_configure(conn)
    assert state is None, state
    with conn.transaction():
        conn.execute(checks._jwt(DEV))
        conn.execute("select public.lab_judge_request_run(%s::uuid, %s::uuid, %s::uuid, %s)",
                     (NEMO, run, cfg["config_id"], d8.PAYER))
        conn.execute("reset role")
    samples = [REQUEST, uid(2, 0x6e)]
    ok(conn, "lab_judge_reserve", {"run_id": run, "provider_org_id": NEMO,
                                   "payer_ref": d8.PAYER, "grant_id": W["grant_id"],
                                   "grant_version": W["grant_version"], "sample_ids": samples,
                                   "media_ids": samples[:1], "price_version": "p",
                                   "max_cost": "10.00000000"})
    ok(conn, "lab_judge_begin_submit", {"run_id": run})
    ok(conn, "lab_judge_record_sent", {"run_id": run, "sample_ids": list(sent)})
    ok(conn, "lab_judge_record_submission", {"run_id": run, "external_id": f"batch-{run}"})
    if result is not None:
        ok(conn, "lab_judge_record_results", {"run_id": run, "results": [
            {"sample_id": REQUEST, "rubric_version": 1, "accepted": True, "result": result}]})
    if actual is not None:
        ok(conn, "lab_judge_settle", {"run_id": run, "actual": actual})


def d8_door_configure(conn):
    grantor = l2.org(conn, C1)
    with conn.transaction():
        conn.execute(checks._jwt(DEV))
        got = conn.execute("select public.lab_judge_configure(%s::uuid, %s::uuid, %s::uuid, "
                           "'judge-1', 1, 20)", (NEMO, grantor, l2.MODEL)).fetchone()[0]
        conn.execute("reset role")
    return None, got


SCORES = {"run_id": "x", "sample_id": REQUEST, "rubric_version": 1, "scores": [
    {"name": "groundedness", "score": 4, "rationale": "the answer quotes the grantor's text"}],
    "overall_pass": True, "notes": "private judge notes", "limited": False,
    "media_required": False}


def seed(conn) -> None:
    d8.seed(conn)
    g = ok(conn, "lab_put_access_grant", l2.scope(
        conn, purposes=["provider_sharing", "training", "external_judging"],
        categories=["request_content", "response_content"]))
    W["grant_id"], W["grant_version"] = g["grant_id"], g["version"]
    # L3's published candidate: a second serving version of the listed model
    W["serving_2"], W["label_2"] = uid(2, 0x5e), "2026-09-28-cand"
    conn.execute(
        "insert into infrx.serving_versions (serving_version_id, model_version_id, model_id, "
        "provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version, "
        "runtime_image_ref, runtime_image_digest, engine_options_digest, precision, capability, "
        "created_by) select %s, model_version_id, model_id, provider_org_id, %s, "
        "prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
        "runtime_image_digest, engine_options_digest, precision, capability, 'd8-test' "
        "from infrx.serving_versions where serving_version_id = %s",
        (W["serving_2"], W["label_2"], cc.SERVING))
    # WR-E8L-2 (R191): a candidate is a deployment of that serving version, not a bare
    # serving version - L3's `operations.serving_ref` pins the deployment, not the revision
    # alone. Deployed private/dev so it never competes with the prod listing above.
    W["deployment_2"] = uid(3, 0x5e)
    conn.execute(
        "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
        "provider_org_id, environment, serving_version_id, visibility, state, "
        "max_input_tokens, max_output_tokens, created_by) values (%s, %s, %s, 'dev', %s, "
        "'private', 'ready_private', 30720, 2048, 'd8-test')",
        (W["deployment_2"], cc.DEV_ENDPOINT, NEMO, W["serving_2"]))
    W["candidate_ref"] = conn.execute(
        "select infrx.lab_serving_ref(%s)", (W["deployment_2"],)).fetchone()[0]


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads the new tables or executes the new infrx RPCs; the
    platform role reads them and writes only through the RPCs; the judge door is
    authenticated's alone and the router's three functions infrx_runtime's alone."""
    probes = [f"select count(*) from infrx.{name}" for name in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached: {reached}"
    unread = [n for n in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(n)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [n for n in TABLES if not (cc.refused_as(conn, "service", f"update infrx.{n} set "
                                                       "provider_org_id = provider_org_id")
                                         or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    acl = conn.execute("select proacl::text from pg_proc where oid = "
                       "'public.lab_judge_runs(uuid, uuid)'::regprocedure").fetchone()[0]
    grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
    assert "authenticated" in grantees and not grantees & {"anon", ""}, acl
    for fn in ("release_active(text)", "release_eligible(uuid, uuid)",
               "record_rollout_assignment(jsonb)"):
        acl = conn.execute("select proacl::text from pg_proc where oid = %s::regprocedure",
                           (f"infrx.{fn}",)).fetchone()[0]
        grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
        assert grantees <= {"postgres", "infrx_runtime"} and "infrx_runtime" in grantees, \
            f"{fn}: {acl}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; door/runtime"


@rolled_back
def check_the_dataset_list_is_the_providers_versions(conn) -> str:
    """WR-N4-2: every dataset version of the provider - ref, id, version, derivation and
    sample count - and none of another provider's."""
    ref = t.publish(conn, t.manifest(uid(1, 0x90), n=3, tag=0x90))
    child = t.publish(conn, t.manifest(uid(1, 0x90), n=2, tag=0x90, version=2, parents=[ref]))
    got = [d for d in ok(conn, "lab_list_datasets", {"provider_org_id": NEMO})
           if d["dataset_id"] == uid(1, 0x90)]
    assert got == [{"ref": ref, "dataset_id": uid(1, 0x90), "version": 1,
                    "derivation": "import", "samples": 3},
                   {"ref": child, "dataset_id": uid(1, 0x90), "version": 2,
                    "derivation": "derive", "samples": 2}], got
    assert not [d for d in ok(conn, "lab_list_datasets", {"provider_org_id": OTHER})
                if d["dataset_id"] == uid(1, 0x90)], "another provider's datasets"
    return "own versions with counts; other provider none"


@rolled_back
def check_runs_carry_their_timeline_and_finished_runs_stay_finished(conn) -> str:
    """WR-B4-2: a run's status carries created_at and updated_at; cancelling a queued run
    moves updated_at; a succeeded run cannot be cancelled (`state_conflict`), a cancelled
    one answers its row again."""
    _, run = t.a_run(conn, n=1, tag=0x91)
    assert run.get("created_at") is not None and run.get("updated_at") == run["created_at"], run
    t.advance(conn, 5)
    cancelled = ok(conn, "lab_cancel_run", {"provider_org_id": NEMO, "run_id": run["run_id"]})
    assert cancelled["state"] == "cancelled" and cancelled.get("updated_at", "") > \
        run["updated_at"]
    assert ok(conn, "lab_cancel_run", {"provider_org_id": NEMO,
                                       "run_id": run["run_id"]})["state"] == "cancelled"
    _, done = t.a_run(conn, n=1, tag=0x92)
    lease = t.lease(conn, done["run_id"])
    ok(conn, "lab_finish_attempt", t.finish(lease))
    assert ok(conn, "lab_run_status", {"provider_org_id": NEMO,
                                       "run_id": done["run_id"]})["state"] == "succeeded"
    assert refusal(conn, "lab_cancel_run", {"provider_org_id": NEMO,
                                            "run_id": done["run_id"]}) == "state_conflict"
    return "timeline carried; finished runs refuse cancel"


@rolled_back
def check_experiments_are_write_once_with_their_report(conn) -> str:
    """WR-B4-2 / EVAL-COMPARE: an experiment names two of the provider's runs and its
    protocol, once (a replay is the row, another launch under the id
    `idempotency_conflict`); the listing shows the runs as they are and the latest stored B2
    report of those runs under that protocol - none from another protocol - and nothing of
    another provider's."""
    base, cand = run_record(conn, 0x93), run_record(conn, 0x94)
    eid = uid(1, 0xe5)
    first = ok(conn, "lab_put_experiment", experiment(eid, base, cand))
    assert ok(conn, "lab_put_experiment", experiment(eid, base, cand)) == first
    assert refusal(conn, "lab_put_experiment", experiment(eid, cand, base)) == \
        "idempotency_conflict"
    other = t.publish(conn, t.eval_run(uid(9, 0x94), t.publish(conn, t.manifest(
        uid(8, 0x94), n=1, tag=0x95)), t.publish(conn, t.harness(uid(7, 0x94)))))
    assert refusal(conn, "lab_put_experiment", {**experiment(uid(2, 0xe5), base, other),
                                                "provider_org_id": OTHER}) == "not_found"
    listed = ok(conn, "lab_experiments", {"provider_org_id": NEMO})
    assert [(e["experiment_id"], e["baseline"], e["report"]) for e in listed] == \
        [(eid, None, None)], listed
    ok(conn, "lab_put_eval_report", {"provider_org_id": NEMO, "actor": "b2",
                                     "body": report(base, cand, "sha256:" + "f" * 64)})
    assert ok(conn, "lab_experiments", {"provider_org_id": NEMO})[0]["report"] is None, \
        "another protocol's report"
    stored = ok(conn, "lab_put_eval_report", {"provider_org_id": NEMO, "actor": "b2",
                                              "body": report(base, cand)})
    got = ok(conn, "lab_experiments", {"provider_org_id": NEMO})[0]["report"]
    assert got["report_digest"] == stored["report_digest"], got
    assert ok(conn, "lab_experiments", {"provider_org_id": OTHER}) == []
    return "write-once experiments; report by runs and protocol; provider-scoped"


@rolled_back
def check_the_checkpoint_listing_is_provider_scoped(conn) -> str:
    """WR-B4-2: the provider's subscriptions without the evaluator spec or the subscriber,
    each with its decisions - checkpoint, training step, receipt state, decision - and
    nothing of another provider's."""
    _, run_ref = d8.external(conn, 0x96)
    sub = {**d8.subscription(uid(1, 0x5c), run_ref), "evaluator": {"metric": "x"}}
    ok(conn, "lab_checkpoint_subscribe", {"subscription": sub})
    cp = uid(1, 0xd0)
    ok(conn, "lab_checkpoint_record_event", {"event": d8.event(cp, run_ref, step=7)})
    ok(conn, "lab_receive_checkpoint", {"provider_org_id": NEMO, "checkpoint_id": cp,
                                        "external_run_ref": run_ref,
                                        "artifact_digest": "sha256:" + "a" * 64})
    ok(conn, "lab_checkpoint_decide", {"subscription_id": uid(1, 0x5c), "checkpoint_id": cp,
                                       "state": "skipped", "reason": "budget", "run_id": None})
    got = ok(conn, "lab_checkpoint_listing", {"provider_org_id": NEMO})
    assert len(got) == 1 and "evaluator" not in got[0]["subscription"] \
        and "owner_user_id" not in got[0]["subscription"], got
    assert got[0]["decisions"] == [{"checkpoint_id": cp, "step": 7, "receipt": "received",
                                    "state": "skipped", "reason": "budget", "run_id": None}], got
    assert ok(conn, "lab_checkpoint_listing", {"provider_org_id": OTHER}) == []
    return "subscriptions with decisions; no evaluator/owner; provider-scoped"


@rolled_back
def check_one_pending_proposal_at_the_displayed_fence(conn) -> str:
    """WR-R4-2 / ROLLOUT-PIN: a proposal is filed against the release revision's CURRENT
    fence (a stale page is `state_conflict`), never on a rolled-back release, `expand` only
    while running; one pending per revision (a second, a double click with a new id, is
    `state_conflict`; the same id again is the row, another body `idempotency_conflict`);
    another provider's release is `not_found`."""
    ref, _ = launch(conn, 1)
    args = {"provider_org_id": NEMO, "proposal_id": uid(1, 0x9f), "policy_ref": ref,
            "kind": "expand", "fence": 1, "proposed_by": ADMIN}
    assert refusal(conn, "lab_propose_release", {**args, "fence": 2}) == "state_conflict"
    assert refusal(conn, "lab_propose_release", {**args, "provider_org_id": OTHER}) == \
        "not_found"
    first = ok(conn, "lab_propose_release", args)
    assert (first["state"], first["fence"], first["kind"]) == ("proposed", 1, "expand"), first
    assert ok(conn, "lab_propose_release", args) == first, "a replay"
    assert refusal(conn, "lab_propose_release", {**args, "kind": "rollback"}) == \
        "idempotency_conflict"
    assert refusal(conn, "lab_propose_release", {**args, "proposal_id": uid(2, 0x9f)}) == \
        "state_conflict", "two pending proposals"
    ok(conn, d9.MOVE, d9.args(uid(1, 0xb0), "pause", 1))
    ref2, _ = launch(conn, 2, endpoint=uid(2, 0xe2))
    ok(conn, d9.MOVE, d9.args(uid(2, 0xb0), "pause", 1))
    assert refusal(conn, "lab_propose_release", {**args, "proposal_id": uid(3, 0x9f),
                                                 "policy_ref": ref2, "fence": 2}) == \
        "state_conflict", "expand on a paused release"
    assert ok(conn, "lab_propose_release", {**args, "proposal_id": uid(4, 0x9f),
                                            "policy_ref": ref2, "fence": 2,
                                            "kind": "rollback"})["state"] == "proposed"
    assert sorted(p["proposal_id"] for p in ok(conn, "lab_release_proposals",
                                               {"provider_org_id": NEMO})) == \
        [uid(1, 0x9f), uid(4, 0x9f)]
    return "current fence; one pending; expand only running"


@rolled_back
def check_a_proposal_is_decided_once_through_the_fence(conn) -> str:
    """WR-R4-2: an approval moves the release only through 0039's CAS at the proposal's
    fence (a release that moved since refuses and the proposal stays pending); a decided
    proposal is never decided again or rewritten; a rejection moves nothing."""
    ref, _ = launch(conn, 3)
    run = d9.evidence(conn, 0xb7)
    propose = {"provider_org_id": NEMO, "proposal_id": uid(5, 0x9f), "policy_ref": ref,
               "kind": "expand", "fence": 1, "proposed_by": ADMIN}
    ok(conn, "lab_propose_release", propose)
    decision = {"schema": "lab.rollout_decision.1", "provider_org_id": NEMO, "policy_ref": ref,
                "decision": "expand", "evidence_refs": [run], "decided_by": ADMIN,
                "decided_at": "2026-09-28T12:00:00Z"}
    decide = {"proposal_id": uid(5, 0x9f), "approve": True, "decision": decision,
              "reasons": ["b2 accept"], "decided_by": ADMIN}
    with conn.transaction(force_rollback=True):
        ok(conn, d9.MOVE, d9.args(uid(3, 0xb0), "pause", 1))
        ok(conn, d9.MOVE, d9.args(uid(3, 0xb0), "resume", 2))       # running again, fence 3
        assert refusal(conn, "lab_decide_release_proposal", decide) == "state_conflict"
        assert ok(conn, "lab_release_proposals", {"provider_org_id": NEMO})[0]["state"] == \
            "proposed"
    got = ok(conn, "lab_decide_release_proposal", decide)
    assert (got["state"], got["decided_by"]) == ("approved", ADMIN), got
    assert ok(conn, "lab_release", {"policy_ref": ref})["state"] == "approved"
    assert refusal(conn, "lab_decide_release_proposal", decide) == "state_conflict"
    rewritten = cc.attempt(conn, "update infrx.lab_release_proposals set state = 'proposed', "
                                 "decided_at = null, decided_by = null")
    assert rewritten is not None, "a decided proposal was reopened"
    ref2, _ = launch(conn, 4, endpoint=uid(4, 0xe4))
    ok(conn, "lab_propose_release", {**propose, "proposal_id": uid(6, 0x9f), "policy_ref": ref2,
                                     "kind": "rollback"})
    rejected = ok(conn, "lab_decide_release_proposal", {"proposal_id": uid(6, 0x9f),
                                                        "approve": False, "decided_by": ADMIN})
    assert rejected["state"] == "rejected"
    assert ok(conn, "lab_release", {"policy_ref": ref2})["state"] == "running"
    return "approved through the CAS; stale refused and pending; decided once"


@rolled_back
def check_the_judge_runs_door(conn) -> str:
    """SR-J3-1 / LAB-ACCESS: flag off is 55000; a viewer, a consumer-only user, another
    provider's member and anon are refused; a developer gets the provider's Lab-requested
    runs that SENT this request - state, judge model, rubric, media, PROVIDER_USD, the
    request's scores without rationale or notes, the pass - and none once the run's grant is
    no longer current; another provider sees none."""
    judged(conn, uid(1, 0x7e), result=SCORES, actual="4.00000000")
    judged(conn, uid(2, 0x7e), sent=(uid(2, 0x6e),))
    state, runs = door(conn, DEV)
    assert state is None and [r["runId"] for r in runs] == [uid(1, 0x7e)], (state, runs)
    run = runs[0]
    assert {k: run[k] for k in ("mode", "state", "judgeModel", "rubricVersion", "media",
                                "reservedUsd", "actualUsd", "overallPass")} == \
        {"mode": "live", "state": "collected", "judgeModel": "judge-1", "rubricVersion": 1,
         "media": True, "reservedUsd": "10.00000000", "actualUsd": "4.00000000",
         "overallPass": True}, run
    assert run["scores"] == [{"criterion": "groundedness", "score": 4, "max": 5,
                              "requiresMedia": False}], run["scores"]
    assert "rationale" not in json.dumps(runs) and "notes" not in json.dumps(runs)
    assert run["calibration"] == UNCALIBRATED, run["calibration"]
    refused = {u: door(conn, u)[0] for u in (VIEWER, C1, BOTH)}
    assert refused == dict.fromkeys(refused, "42501"), refused
    assert door(conn, None)[0] == "42501", "anon"
    assert door(conn, BOTH, OTHER) == (None, []), "another provider's runs"
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": l2.org(conn, C1),
                                         "recipient_provider_org_id": NEMO})
    assert door(conn, DEV) == (None, []), "runs shown after revocation"
    conn.execute("update infrx.feature_flags set enabled = false where name = 'lab_submission'")
    assert door(conn, DEV)[0] == "55000"
    return "developer only; own provider; sent request; no rationale; revocation hides"


@rolled_back
def check_calibrations_are_j3s_and_the_latest_stands(conn) -> str:
    """SR-J3-1 / JUDGE-SCORES: the door shows J3's latest calibration of the run's own
    configuration (grantor, judge model, rubric version) - uncalibrated until one exists -
    and a `calibrated` claim without enough labels, an agreement and its interval is
    refused."""
    judged(conn, uid(3, 0x7e), result=SCORES)
    put = {"provider_org_id": NEMO, "grantor_org_id": l2.org(conn, C1), "judge_model": "judge-1",
           "rubric_version": 1}
    calibrated = {"state": "calibrated", "labels": 40, "required": 30, "agreement": 0.8,
                  "interval": [0.65, 0.95]}
    ok(conn, "lab_put_judge_calibration", {**put, "calibration": {
        "state": "insufficient", "labels": 12, "required": 30, "agreement": 0.5,
        "interval": [0.1, 0.9]}})
    ok(conn, "lab_put_judge_calibration", {**put, "calibration": calibrated})
    ok(conn, "lab_put_judge_calibration", {**put, "judge_model": "judge-2",
                                           "calibration": UNCALIBRATED})
    assert door(conn, DEV)[1][0]["calibration"] == calibrated
    unsupported = {**calibrated, "labels": 29}
    assert refusal(conn, "lab_put_judge_calibration", {**put, "calibration": unsupported}) == \
        "invalid_request"
    assert refusal(conn, "lab_put_judge_calibration", {**put, "calibration": {
        **calibrated, "interval": None}}) == "invalid_request"
    return "latest of the run's configuration; unsupported claims refused"


@rolled_back
def check_release_active_answers_the_running_head_with_pins(conn) -> str:
    """SR-R1-1 / ROLLOUT-PIN: for an alias, the running rollout of the endpoint its current
    listing serves - the policy record, its ref, each candidate's R62 pin
    (`<alias>@<revision>`) and the shadow bound (0 by default) - and nothing for a paused
    rollout or an unknown alias; a candidate that is not a published revision of the model,
    or whose digest is stale for its deployment (R191), is `state_conflict` (fail closed);
    only infrx_runtime executes it."""
    assert active(conn) == (None, []), "a release before any rollout"
    stale_digest = W["candidate_ref"].rpartition("sha256:")[0] + "sha256:" + "f" * 64
    stale = {**policy(4), "candidates": [{"serving_ref": stale_digest, "weight_bp": 100}]}
    ok(conn, "lab_rollout_start", {"provider_org_id": NEMO, "policy_ref": t.publish(conn, stale),
                                   "decided_by": DEV, "reason": "x"})
    assert active(conn)[0] == "P0001", "a stale-digest candidate (the revision moved on) routed"
    ok(conn, d9.MOVE, d9.args(uid(4, 0xb0), "stop", 1))
    ref, body = launch(conn, 5)
    state, rows = active(conn)
    assert state is None and len(rows) == 1, (state, rows)
    record, policy_ref, revisions, shadow_limit = rows[0]
    assert (record, policy_ref, shadow_limit) == (body, ref, 0), rows
    assert revisions == {W["candidate_ref"]: f"{ALIAS}@{W['label_2']}"}, revisions
    assert active(conn, "nemostation/unknown") == (None, [])
    with conn.transaction(force_rollback=True):                   # the alias moved on
        execute_all(conn,
            "insert into infrx.endpoints (endpoint_id, provider_org_id, name, environment, "
            "created_by) values (%(e)s, %(p)s, 'marlin-2b-b', 'prod', 't');"
            "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
            "provider_org_id, environment, serving_version_id, visibility, state, "
            "max_input_tokens, max_output_tokens, created_by) values (%(d)s, %(e)s, %(p)s, "
            "'prod', %(s)s, 'public', 'active', 30720, 2048, 't');"
            "insert into infrx.rate_card_versions (rate_card_version, model_id, "
            "deployment_revision_id, serving_version_id, input_rate_per_million, "
            "output_rate_per_million, effective_at, approved_by, provisional) values "
            "('rc_d8_b', %(m)s, %(d)s, %(s)s, 400, 1200, '2026-09-01', 't', true);"
            "insert into infrx.catalog_listings (public_model_id, version, model_id, "
            "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
            "approved_by) values (%(a)s, 2, %(m)s, %(d)s, %(s)s, 'rc_d8_b', '2026-09-01', 't')",
            {"e": uid(1, 0xed), "d": uid(2, 0xed), "p": NEMO, "s": cc.SERVING, "m": cc.MODEL,
             "a": ALIAS})
        assert active(conn) == (None, []), "the alias routed by an outdated listing"
    denied = cc.refused_as(conn, "service", f"select * from infrx.release_active('{ALIAS}')")
    assert denied is not None and denied.startswith("42501"), denied
    ok(conn, d9.MOVE, d9.args(uid(5, 0xb0), "pause", 1))
    assert active(conn) == (None, []), "a paused rollout routes"
    ok(conn, d9.MOVE, d9.args(uid(5, 0xb0), "stop", 2))
    foreign = {**policy(6), "candidates": [{"serving_ref": serving_ref(uid(9, 0x5e)),
                                            "weight_bp": 100}]}
    fref = t.publish(conn, foreign)
    ok(conn, "lab_rollout_start", {"provider_org_id": NEMO, "policy_ref": fref,
                                   "decided_by": DEV, "reason": "x"})
    assert active(conn)[0] == "P0001", "an unpinned candidate routed"
    ok(conn, d9.MOVE, d9.args(uid(6, 0xb0), "stop", 1))
    other_model = uid(3, 0x5e)
    execute_all(conn,
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, model_uuid, provider_org_id) values ('nemostation/other', 'o', "
        "'o', 'o', 'live', 'https://o.example', 'o', 1, 1, 1024, '{text}', '{text}', %(m)s, "
        "%(p)s);"
        "insert into infrx.model_versions (model_version_id, model_id, provider_org_id, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, created_by) select %(v)s, %(m)s, %(p)s, "
        "model_repo, model_commit, weight_shard_digests, tokenizer_digest, "
        "chat_template_digest, digest_source, 't' from infrx.model_versions "
        "where model_id = %(base)s limit 1;"
        "insert into infrx.serving_versions (serving_version_id, model_version_id, model_id, "
        "provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version, "
        "runtime_image_ref, runtime_image_digest, engine_options_digest, precision, capability, "
        "created_by) select %(s)s, %(v)s, %(m)s, provider_org_id, 'other-1', "
        "prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
        "runtime_image_digest, engine_options_digest, precision, capability, 't' "
        "from infrx.serving_versions where serving_version_id = %(sv)s;"
        "insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
        "provider_org_id, environment, serving_version_id, visibility, state, "
        "max_input_tokens, max_output_tokens, created_by) values (%(dep)s, %(ep)s, %(p)s, "
        "'dev', %(s)s, 'private', 'ready_private', 1, 1, 't')",
        {"m": other_model, "p": NEMO, "v": uid(4, 0x5e), "s": uid(5, 0x5e),
         "base": cc.MODEL, "sv": cc.SERVING, "dep": uid(6, 0x5e), "ep": cc.DEV_ENDPOINT})
    other_ref = conn.execute("select infrx.lab_serving_ref(%s)", (uid(6, 0x5e),)).fetchone()[0]
    wrong = {**policy(10), "candidates": [{"serving_ref": other_ref, "weight_bp": 100}]}
    ok(conn, "lab_rollout_start", {"provider_org_id": NEMO, "policy_ref": t.publish(conn, wrong),
                                   "decided_by": DEV, "reason": "x"})
    assert active(conn)[0] == "P0001", (
        "another model's revision pinned under the alias - real deployment, real digest, "
        "wrong model")
    return "running head with pins; paused/unknown none; unpinned fails closed"


@rolled_back
def check_release_eligibility_is_current_and_default_deny(conn) -> str:
    """SR-R1-1: a subject is eligible only while its organization holds a CURRENT
    provider_sharing grant to the policy's provider and is not suspended - revocation,
    a narrowed grant, expiry, suspension or no grant at all deny, read now."""
    launch(conn, 7)
    pid, c1org = uid(7, 0xb0), l2.org(conn, C1)
    assert eligible(conn, pid, c1org) is True
    assert eligible(conn, pid, l2.org(conn, BOTH)) is False, "no grant to NEMO"
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["capture"]))
    assert eligible(conn, pid, c1org) is False, "a capture-only grant"
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["provider_sharing"]))
    assert eligible(conn, pid, c1org) is True
    with conn.transaction(force_rollback=True):
        conn.execute("update public.organizations set suspended = true, suspended_at = "
                     "infrx.now(), suspension_reason = 'abuse' where id = %s", (c1org,))
        assert eligible(conn, pid, c1org) is False, "a suspended organization"
    with conn.transaction(force_rollback=True):
        ok(conn, "lab_put_access_grant", l2.scope(
            conn, purposes=["provider_sharing"],
            expires_at=conn.execute("select infrx.now() + interval '60 seconds'"
                                    ).fetchone()[0].isoformat()))
        assert eligible(conn, pid, c1org) is True, "a grant not yet expired"
        t.advance(conn, 61)
        assert eligible(conn, pid, c1org) is False, "an expired grant"
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": c1org,
                                         "recipient_provider_org_id": NEMO})
    assert eligible(conn, pid, c1org) is False, "a revoked grant"
    assert eligible(conn, uid(99, 0xb0), c1org) is False, "an unknown policy"
    return "current provider_sharing grant only; default deny"


@rolled_back
def check_the_operator_raises_the_shadow_limit_and_only_the_operator(conn) -> str:
    """WR-E8L-4: `public.operator_raise_lab_shadow_limit` moves `lab_rollouts.shadow_limit`
    up only (never down, and a call at or below the bound in force is a no-op naming the
    unchanged bound), under `infrx.console_operator`'s door (0025) - not a provider, not an
    anonymous caller, only a platform operator."""
    from . import checks_operator as op
    ref, body = launch(conn, 9)
    pid = body["policy_id"]
    op.make_operator(conn)
    sql = "select public.operator_raise_lab_shadow_limit(%s, %s, %s, %s)"
    code, answer, _ = op.call(conn, op.OPERATOR, sql, (pid, 5, "e8l drill", "shadow-1"))
    assert code is None and answer == {"policy_id": pid, "shadow_limit": 5}, (code, answer)
    lowered = op.call(conn, op.OPERATOR, sql, (pid, 2, "e8l drill", "shadow-2"))
    assert lowered == (None, {"policy_id": pid, "shadow_limit": 5}, ""), \
        "a call at or below the bound in force never lowers it"
    assert conn.execute("select shadow_limit from infrx.lab_rollouts where policy_id = %s",
                        (pid,)).fetchone() == (5,)
    assert op.call(conn, DEV, sql, (pid, 9, "not an operator", "shadow-3"))[0] == "42501", \
        "an authenticated non-operator"
    assert op.call(conn, "anon", sql, (pid, 9, "x", "shadow-4"))[0] == "42501", "anonymous"
    unknown = op.call(conn, op.OPERATOR, sql, (uid(99, 0xb0), 1, "e8l drill", "shadow-5"))
    assert unknown[0] == "not_found", unknown
    negative = op.call(conn, op.OPERATOR, sql, (pid, -1, "e8l drill", "shadow-6"))
    assert negative[0] == "invalid_request", negative
    return "raises only, monotonic, replay-safe by construction; operator door holds"


@rolled_back
def check_assignments_are_recorded_once_for_the_runtime(conn) -> str:
    """SR-R1-1: an admitted request's assignment is recorded once (a retry records nothing
    new and never changes the serving), only for a serving the revision offers, under the
    provider's own rollout; no org, user or key is stored."""
    ref, body = launch(conn, 8)
    cand = body["candidates"][0]["serving_ref"]
    rec = assignment(ref, uid(1, 0xa8), cand)
    sql = "select infrx.record_rollout_assignment(%s)"
    assert as_runtime(conn, sql, (Jsonb(rec),))[0] is None
    assert as_runtime(conn, sql, (Jsonb({**rec, "serving_ref": body["baseline_ref"]}),))[0] \
        is None, "a retry"
    rows = conn.execute("select serving_ref, pinned_by from infrx.lab_rollout_assignments "
                        "where request_id = %s", (uid(1, 0xa8),)).fetchall()
    assert rows == [(cand, "cohort")], rows
    bad = as_runtime(conn, sql, (Jsonb({**assignment(ref, uid(2, 0xa8), serving_ref(
        uid(8, 0x5e)))}),))
    assert bad == ("P0001", "invalid_request"), bad
    foreign = as_runtime(conn, sql, (Jsonb({**rec, "request_id": uid(3, 0xa8),
                                            "provider_org_id": OTHER}),))
    assert foreign == ("P0001", "not_found"), foreign
    return "once per request; offered servings only; provider's own"


def as_control_login(dsn: str):
    """The control service's `Connect` on its own login (the factory's
    `connector(INFRX_LAB_DATABASE_URL, set_role=False)`, WR-I2L-4b): no `set role
    service_role`, only what the login itself holds. `dsn` is `pgharness.login`'s."""
    async def connect():
        return await psycopg.AsyncConnection.connect(dsn, autocommit=True,
                                                     prepare_threshold=None)
    return connect


@rolled_back
def check_the_control_login_is_bounded_and_lab_only(conn) -> str:
    """WR-I2L-4 / OPS-RECOVER: the control service's login exists with a connection limit,
    no bypass of row security; the factory's own calls (the /readyz clock, the model's
    provider, the serving revision, the price, memberships, the control events) answer on
    it, and it reaches neither service_role, the consumer's consent writes, the content
    service's refs, another Lab RPC, nor the App's RPCs and tables."""
    # read before `pgharness.login`, which gives the role LOGIN for the check's sessions
    row = conn.execute("select rolcanlogin, rolconnlimit, rolbypassrls, rolsuper from pg_roles "
                       "where rolname = 'infrx_lab_control'").fetchone()
    assert row is not None and row[0] and 0 < row[1] <= 10 and not row[2] and not row[3], row
    with pgharness.login("infrx_lab_control", conn.info.dbname) as dsn:
        return _bounded_and_lab_only(conn, dsn, row[1])


def _bounded_and_lab_only(conn, dsn: str, limit: int) -> str:
    import asyncio

    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_control import PgControlStore
    model, provider, serving = conn.execute(
        "select m.model_uuid::text, m.provider_org_id::text, s.serving_version_id::text "
        "from infrx.serving_versions s join public.models m on m.model_uuid = s.model_id "
        "limit 1").fetchone()
    login = as_control_login(dsn)
    store, catalog, access = PgControlStore(login), PgCatalogDirectory(login), PgAccessStore(login)

    async def attempt(coro, answer=lambda got: got):
        try:
            return answer(await coro)
        except Exception as failed:                     # noqa: BLE001 - the answer is its type
            return type(failed).__name__

    async def factory_calls():
        return (await attempt(store.db_now(), lambda at: at is not None),
                await attempt(store.model_provider(model)),
                await attempt(catalog.serving_revision(serving),
                              lambda got: getattr(got, "serving_version_id", None)),
                await attempt(catalog.usd_price("no-such-model")),
                await attempt(access.memberships_for_user(DEV),
                              lambda got: [m.provider_org_id for m in got]),
                await attempt(store.events(NEMO), lambda got: isinstance(got, list)))
    got = asyncio.run(factory_calls())
    assert got == (True, provider, serving, None, [NEMO], True), got
    writes = conn.execute(
        "select bool_and(has_table_privilege('infrx_lab_control', t, 'INSERT')) from "
        "unnest(array['infrx.model_versions', 'infrx.serving_versions', "
        "'infrx.deployment_revisions', 'infrx.rate_card_versions']) t").fetchone()[0]
    assert writes, "PgRegistry.put cannot write the registry on the control login"

    def as_control(sql: str) -> str | None:
        try:
            with psycopg.connect(dsn) as control, control.transaction(force_rollback=True):
                control.execute(sql)
        except psycopg.Error as refused:
            return refused.sqlstate
        return None
    lab = "'{}'::jsonb"
    refused = {"service_role": "set role service_role",
               "put_grant": f"select infrx.lab_put_access_grant({lab})",
               "revoke_grant": f"select infrx.lab_revoke_access_grant({lab})",
               "ref_issue": f"select infrx.lab_content_ref_issue({lab})",
               "ref_redeem": f"select infrx.lab_content_ref_redeem({lab})",
               "ref_held": f"select infrx.lab_content_ref_held({lab})",
               # 0066 (SR-AP10-3) grants the route reads; a worker-only door stays out
               "other_lab": f"select infrx.lab_tombstone_samples({lab})",
               "admit": "select infrx.admit('{}'::jsonb)",
               "jobs": "select count(*) from infrx.jobs",
               "wallets": "select count(*) from infrx.credit_wallets",
               "api_keys": "select count(*) from public.api_keys",
               "registry_update": "update infrx.serving_versions set revision_label = 'x'"}
    got = {k: as_control(v) for k, v in refused.items()}
    assert got == dict.fromkeys(refused, "42501"), got
    return f"login, limit {limit}; the factory's calls answer; nothing else"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_the_dataset_list_is_the_providers_versions,
    check_runs_carry_their_timeline_and_finished_runs_stay_finished,
    check_experiments_are_write_once_with_their_report,
    check_the_checkpoint_listing_is_provider_scoped,
    check_one_pending_proposal_at_the_displayed_fence,
    check_a_proposal_is_decided_once_through_the_fence,
    check_the_judge_runs_door,
    check_calibrations_are_j3s_and_the_latest_stands,
    check_release_active_answers_the_running_head_with_pins,
    check_release_eligibility_is_current_and_default_deny,
    check_the_operator_raises_the_shadow_limit_and_only_the_operator,
    check_assignments_are_recorded_once_for_the_runtime,
    check_the_control_login_is_bounded_and_lab_only)}


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_d8_requests(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
