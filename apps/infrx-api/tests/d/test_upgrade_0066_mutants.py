#!/usr/bin/env python3
"""AP-00 00d remainder (lane api-schema-2, wave 7 batch 2, R271): `0066_wave7_grants_and_reads.sql`
on real PostgreSQL over L3-SQL's world (NemoStation, OTHER, their deployments, L2's grantors)
- SR-AP10-1 (the evaluation catalog; the experiment listing's run refs), SR-AP10-2 (the
pipeline listings), SR-AP10-3 (the control login's EXECUTE), AP-07's revoke-only door - each
check, the SQL mutation list that must break it (R32/R40; needs Docker, skips visibly
without it; plain PostgreSQL and `INFRX_D1_IMAGE=supabase`), and the guards. 0066's
`control_op_cancel` is proven by `test_control_ops.py` (its mutants in
`test_control_ops_mutants.py`); the whole-set R151 rehearsal is `test_control_ops_upgrade.py`.

    INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_upgrade_0066_mutants.py
"""
from __future__ import annotations

import hashlib
import json

import pytest
from psycopg.types.json import Jsonb

from infrx.state import migrations

from . import checks_credit as cc
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_l2sql_access as l2
from . import test_l3sql_control as l3
from .test_upgrade_0065_mutants import LOGIN, OTHERS, _can, as_login

FILE = "0066_wave7_grants_and_reads.sql"
DB, DB_MUT = f"{pgharness.DATABASE}_0066", f"{pgharness.DATABASE}_0066mut"
_reason = pgharness.unavailable()
NEMO, OTHER = l3.NEMO, l3.OTHER
#: SR-AP10-3: the route reads 0043/0034/0042 had but the control login lacked, and 0066's own.
CONTROL_READS = ("lab_put_experiment", "lab_checkpoint_listing", "lab_list_datasets",
                 "lab_evaluator", "lab_checkpoint_subscribe", "lab_checkpoint_decisions",
                 "lab_eval_catalog", "lab_external_runs_of", "lab_checkpoint_receipts_of")
WITHDRAW = "infrx.lab_withdraw_access_grant(jsonb)"
HASH = "sha256:" + "a" * 64


def _uid(n: int) -> str:
    return f"e0660000-0000-4000-8000-{n:012d}"


def record(conn, kind: str, provider: str, n: int, body: dict, version: int | None = 1) -> str:
    """A content-addressed `lab_records` row (0029), as `lab_publish` would store it."""
    text = json.dumps(body, sort_keys=True)
    ref = (f"lab:{kind}:{provider}:{_uid(n)}@sha256:"
           f"{hashlib.sha256(text.encode()).hexdigest()}")
    conn.execute("insert into infrx.lab_records (ref, kind, provider_org_id, object_id, version, "
                 "schema_id, body, published_by) values (%s, %s, %s, %s, %s, 'lab.x.1', %s, "
                 "'dev@nemo')", (ref, kind, provider, _uid(n), version, text))
    return ref


def evaluator(conn, provider: str, n: int, metric: str = "exact_match") -> str:
    text = json.dumps({"metric": metric})
    ref = (f"lab:evaluator:{provider}:{_uid(n)}@sha256:"
           f"{hashlib.sha256(text.encode()).hexdigest()}")
    conn.execute("insert into infrx.lab_evaluators (ref, provider_org_id, evaluator_id, body, "
                 "published_by) values (%s, %s, %s, %s, 'dev@nemo')", (ref, provider, _uid(n), text))
    return ref


def door(conn, fn: str, provider: str):
    return as_login(conn, f"select infrx.{fn}(%s)", (Jsonb({"provider_org_id": provider}),))


# ----------------------------------------------------------------------------- checks
@l2.rolled_back
def check_the_eval_catalog_offers_only_the_providers_launchables(conn) -> str:
    """SR-AP10-1 (a): the launch catalog is the provider's own dataset and harness records,
    its evaluators and its READY PRIVATE DEV deployment revisions (by serving ref) - never a
    draft, a public proposal, a private production revision or another provider's; each with the fields the route checks a
    launch against."""
    dataset = record(conn, "dataset", NEMO, 1, {"samples": []}, version=2)
    harness = record(conn, "harness", NEMO, 2, {"adapter": "finite_video"}, version=3)
    record(conn, "dataset", OTHER, 3, {"samples": []})
    spec = evaluator(conn, NEMO, 4)
    evaluator(conn, OTHER, 5, "other_metric")
    ready = conn.execute("select infrx.lab_serving_ref(%s)", (cc.DEV_DEPLOYMENT,)).fetchone()[0]
    before = door(conn, "lab_eval_catalog", NEMO)
    assert [s["ref"] for s in before[0][0]["servings"]] == [ready], \
        f"only the world's ready private dev revision (not draft D2, not public P2): {before}"
    conn.execute("insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
                 "provider_org_id, environment, serving_version_id, visibility, state, "
                 "max_input_tokens, max_output_tokens, created_by) select %s, %s, provider_org_id, "
                 "'prod', serving_version_id, 'private', 'ready_private', 1, 1, 'dev@nemo' "
                 "from infrx.deployment_revisions where deployment_revision_id = %s",
                 (_uid(15), cc.PROD_ENDPOINT, l3.D2))  # private but PROD: never evaluated
    l3.validated(conn)                                   # D2: draft -> ready_private (dev)
    serving = conn.execute("select infrx.lab_serving_ref(%s)", (l3.D2,)).fetchone()[0]
    got = door(conn, "lab_eval_catalog", NEMO)
    assert got == [({
        "datasets": [{"ref": dataset, "label": f"{_uid(1)} v2"}],
        "harnesses": [{"ref": harness, "harness_id": _uid(2), "version": 3,
                       "adapter": "finite_video"}],
        "evaluators": [{"ref": spec, "label": "exact_match"}],
        "servings": [{"ref": ready, "label": "2026-09-01"},
                     {"ref": serving, "label": "2026-10-01"}]},)], got
    other = door(conn, "lab_eval_catalog", OTHER)[0][0]
    assert ([d["label"] for d in other["datasets"]], other["harnesses"],
            [e["label"] for e in other["evaluators"]], other["servings"]) == \
        ([f"{_uid(3)} v1"], [], ["other_metric"], []), other
    return "own datasets/harnesses/evaluators + ready private dev servings only"


@l2.rolled_back
def check_an_experiment_listing_names_its_two_runs(conn) -> str:
    """SR-AP10-1 (b): an experiment stored before `freeze` created its runs (0043 keys it by
    its two published run records) lists its run refs, so a resubmit can find them; the
    control login stores and lists it (SR-AP10-3); a report under another protocol is not
    this experiment's; another provider lists nothing."""
    spec = evaluator(conn, NEMO, 6)
    runs = [record(conn, "run", NEMO, n, {"evaluator_ref": spec, "arm": n}) for n in (7, 8)]
    protocol = {"metric": "exact_match"}
    put = {"provider_org_id": NEMO, "experiment_id": _uid(9), "protocol": protocol,
           "protocol_digest": HASH, "baseline_run_ref": runs[0], "candidate_run_ref": runs[1],
           "actor": "dev@nemo"}
    stored = as_login(conn, "select infrx.lab_put_experiment(%s)->>'experiment_id'",
                      (Jsonb(put),), keep=True)
    assert stored == [(_uid(9),)], stored
    body = json.dumps({"other": True})
    conn.execute("insert into infrx.lab_eval_reports (report_digest, provider_org_id, "
                 "baseline_run_ref, candidate_run_ref, protocol_digest, outcome, body, stored_by) "
                 "values (%s, %s, %s, %s, %s, 'accept', %s, 'dev@nemo')",
                 ("sha256:" + hashlib.sha256(body.encode()).hexdigest(), NEMO, runs[0], runs[1],
                  "sha256:" + "b" * 64, body))
    listed = door(conn, "lab_experiments", NEMO)[0][0]
    assert [(e.get("experiment_id"), e.get("baseline_run_ref"), e.get("candidate_run_ref"),
             e.get("baseline"), e.get("candidate"), e.get("report")) for e in listed] == \
        [(_uid(9), runs[0], runs[1], None, None, None)], listed
    assert door(conn, "lab_experiments", OTHER) == [([],)]
    return "refs listed before freeze; another protocol's report and provider excluded"


@l2.rolled_back
def check_the_pipeline_listings_are_the_providers_own(conn) -> str:
    """SR-AP10-2 (register row 15, WR-LAB2-4): the provider's external runs as
    (external_run_id, doc) and its checkpoint receipts with P3's outcome note (the note's
    state and reason override the receipt's), oldest first; never another provider's run,
    receipt or note (OTHER noted a checkpoint id NEMO also received)."""
    rows = []
    for n, provider in ((10, NEMO), (11, OTHER)):
        ref = record(conn, "external_run", provider, n, {"n": n})
        doc = {"state": "submitted", "run_ref": ref, "n": n}
        conn.execute("insert into infrx.lab_external_runs (provider_org_id, external_run_id, "
                     "run_ref, state, doc) values (%s, %s, %s, 'submitted', %s)",
                     (provider, _uid(n), ref, Jsonb(doc)))
        rows.append((ref, doc))
    for n, note in ((12, {"state": "rejected", "reason": "incompatible"}), (13, None)):
        conn.execute("insert into infrx.lab_checkpoint_receipts (checkpoint_id, provider_org_id, "
                     "external_run_ref, artifact_digest, received_at) values (%s, %s, %s, %s, "
                     "infrx.now() + make_interval(secs => %s))", (_uid(n), NEMO, rows[0][0], HASH, n))
        if note:
            conn.execute("insert into infrx.lab_pipeline_notes (provider_org_id, key, body) "
                         "values (%s, %s, %s)", (NEMO, f"checkpoint:{_uid(n)}", Jsonb(note)))
    conn.execute("insert into infrx.lab_checkpoint_receipts (checkpoint_id, provider_org_id, "
                 "external_run_ref, artifact_digest) values (%s, %s, %s, %s)",
                 (_uid(14), OTHER, rows[1][0], HASH))
    conn.execute("insert into infrx.lab_pipeline_notes (provider_org_id, key, body) values "
                 "(%s, %s, %s)", (OTHER, f"checkpoint:{_uid(13)}", Jsonb({"state": "evaluated"})))
    runs = door(conn, "lab_external_runs_of", NEMO)
    assert runs == [([{"external_run_id": _uid(10), "doc": rows[0][1]}],)], runs
    receipts = door(conn, "lab_checkpoint_receipts_of", NEMO)
    assert receipts == [([
        {"checkpoint_id": _uid(12), "external_run_ref": rows[0][0], "artifact_digest": HASH,
         "state": "rejected", "reason": "incompatible"},
        {"checkpoint_id": _uid(13), "external_run_ref": rows[0][0], "artifact_digest": HASH,
         "state": "received", "reason": None}],)], receipts
    return "one run, two receipts (one noted) of NEMO; OTHER's excluded"


def check_the_control_login_executes_the_route_reads(conn) -> str:
    """SR-AP10-3 (R251): the control login and the platform role execute the nine route
    reads; no browser role, nor the gateway's or monitor's dedicated login, executes them or
    the withdrawal door, which only the platform role (the console's data-use pool) runs."""
    reads = [f"infrx.{n}(jsonb)" for n in CONTROL_READS]
    missing = [f"{r} {f}" for r in ("service_role", LOGIN) for f in reads
               if not _can(conn, r, f)]
    assert not missing, f"refused: {missing}"
    reached = [f"{r} {f}" for r in (*OTHERS, LOGIN) for f in (*reads, WITHDRAW)
               if _can(conn, r, f) and not (r == LOGIN and f != WITHDRAW)]
    assert not reached and _can(conn, "service_role", WITHDRAW), f"reached: {reached}"
    return f"{len(reads)} reads: service_role + {LOGIN}; withdrawal: service_role only"


@l2.rolled_back
def check_a_suspended_grantor_withdraws_but_never_shares(conn) -> str:
    """AP-07 (R33 + privacy): a suspended organization's owner can no longer grant or
    re-scope (0027, unchanged) but withdraws a current grant through the revoke-only door -
    the next version, revoked now; a second withdrawal, a never-granted pair and anyone but
    the grantor's owner are refused."""
    granted = l2.ok(conn, "lab_put_access_grant", l2.scope(conn))
    conn.execute("update public.organizations set suspended = true, suspended_at = now(), "
                 "suspension_reason = 'other' where id = %s", (l2.org(conn, l2.C1),))
    who = {"actor_user_id": l2.C1, "grantor_org_id": l2.org(conn, l2.C1),
           "recipient_provider_org_id": NEMO}
    assert (l2.refusal(conn, "lab_put_access_grant", l2.scope(conn)),
            l2.refusal(conn, "lab_revoke_access_grant", who)) == ("org_suspended",) * 2
    stranger = {**who, "actor_user_id": l2.DEV}
    assert l2.refusal(conn, "lab_withdraw_access_grant", stranger) == "forbidden"
    gone = l2.ok(conn, "lab_withdraw_access_grant", who)
    assert (gone["version"], gone["grant_id"], gone["revoked_at"] is not None,
            gone["purposes"]) == (granted["version"] + 1, granted["grant_id"], True,
                                  granted["purposes"]), gone
    assert l2.refusal(conn, "lab_withdraw_access_grant", who) == "state_conflict"
    assert l2.refusal(conn, "lab_withdraw_access_grant",
                      {**who, "recipient_provider_org_id": OTHER}) == "state_conflict"
    return "suspended: put/revoke refused; withdraw once by the owner -> next version revoked"


CHECKS = {c.__name__: c for c in (
    check_the_eval_catalog_offers_only_the_providers_launchables,
    check_an_experiment_listing_names_its_two_runs,
    check_the_pipeline_listings_are_the_providers_own,
    check_the_control_login_executes_the_route_reads,
    check_a_suspended_grantor_withdraws_but_never_shares)}
CATALOG, EXPERIMENTS, PIPELINES, GRANTS, WITHDRAWAL = CHECKS


def seed(conn) -> None:
    l3.seed(conn)


# ----------------------------------------------------------------------------- mutants
def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


GRANT_READS = ("grant execute on function infrx.lab_eval_catalog(jsonb), "
               "infrx.lab_external_runs_of(jsonb),\n  infrx.lab_checkpoint_receipts_of(jsonb)\n"
               "  to infrx_lab_control;")
SQL_MUTANTS = (
    # --- SR-AP10-1 (a): the catalog
    _s("ap0066_datasets_any_provider", "                  where r.kind = 'dataset' and "
       "r.provider_org_id::text = p_args->>'provider_org_id'),", "                  where "
       "r.kind = 'dataset'),", CATALOG, "a provider launches on another provider's dataset"),
    _s("ap0066_harness_any_kind", "                   where r.kind = 'harness' and",
       "                   where r.kind in ('harness', 'dataset') and", CATALOG,
       "a dataset is offered as a harness (the launch fails in the worker)"),
    _s("ap0066_evaluators_any_provider", "                     from infrx.lab_evaluators e\n"
       "                    where e.provider_org_id::text = p_args->>'provider_org_id'),",
       "                     from infrx.lab_evaluators e),", CATALOG,
       "another provider's evaluator spec is offered"),
    _s("ap0066_servings_any_state", "\n                    and d.state = 'ready_private'))", "))",
       CATALOG, "a draft or retired dev revision is offered as a serving to evaluate"),
    _s("ap0066_servings_public", "                    and d.environment = 'dev' and "
       "d.visibility = 'private'\n", "", CATALOG,
       "an evaluation targets a production endpoint (lab.eval_run.1: dev only)"),
    _s("ap0066_servings_any_provider", "                  where d.provider_org_id::text = "
       "p_args->>'provider_org_id'\n", "                  where true\n", CATALOG,
       "a provider evaluates against another provider's private deployment"),
    _s("ap0066_harness_no_adapter", "                    'adapter', r.body::jsonb->>'adapter')",
       "                    'adapter', null)", CATALOG, "the Lab cannot tell a video harness"),
    # --- SR-AP10-1 (b): the experiment listing
    _s("ap0066_no_run_refs", "      'baseline_run_ref', e.baseline_run_ref, 'candidate_run_ref', "
       "e.candidate_run_ref,\n", "", EXPERIMENTS,
       "an experiment stored before freeze cannot be read or resubmitted (503)"),
    _s("ap0066_experiments_unscoped", "    from infrx.lab_experiments e where "
       "e.provider_org_id::text = p_args->>'provider_org_id'\n", "    from "
       "infrx.lab_experiments e where true\n", EXPERIMENTS,
       "a provider lists another's experiments (d8's q_experiments_unscoped, moved)"),
    _s("ap0066_experiment_any_report", "                    and p.protocol_digest = "
       "e.protocol_digest\n", "", EXPERIMENTS,
       "a report under another protocol is shown as this experiment's (d8's, moved)"),
    # --- SR-AP10-2: the pipeline listings
    _s("ap0066_runs_unscoped", "    from infrx.lab_external_runs o where o.provider_org_id::text "
       "= p_args->>'provider_org_id'", "    from infrx.lab_external_runs o where true", PIPELINES,
       "a provider lists another's training runs"),
    _s("ap0066_receipts_unscoped", "   where rc.provider_org_id::text = "
       "p_args->>'provider_org_id'", "   where true", PIPELINES,
       "a provider lists another's checkpoints"),
    _s("ap0066_note_ignored", "'state', coalesce(n.body->>'state', rc.state), 'reason', "
       "n.body->>'reason')", "'state', rc.state, 'reason', n.body->>'reason')", PIPELINES,
       "a rejected checkpoint reads as merely received"),
    _s("ap0066_note_of_another_provider", "    left join infrx.lab_pipeline_notes n on "
       "n.provider_org_id = rc.provider_org_id\n     and", "    left join "
       "infrx.lab_pipeline_notes n on true\n     and", PIPELINES,
       "another provider's note decides this checkpoint's state"),
    # --- SR-AP10-3: grants
    _s("ap0066_put_experiment_refused", "grant execute on function "
       "infrx.lab_put_experiment(jsonb), infrx.lab_checkpoint_listing(jsonb),",
       "grant execute on function infrx.lab_checkpoint_listing(jsonb),", GRANTS,
       "POST /lab/v1/evaluations/experiments answers 503 on the unit"),
    _s("ap0066_catalog_refused", GRANT_READS, "", GRANTS,
       "the launch catalog answers 503 on the unit"),
    _s("ap0066_reads_to_authenticated", GRANT_READS, GRANT_READS.replace(
        "  to infrx_lab_control;", "  to infrx_lab_control, authenticated;"), GRANTS,
       "a browser session lists any provider's catalog and runs"),
    _s("ap0066_withdraw_to_control", "  infrx.lab_checkpoint_receipts_of(jsonb)\n  to "
       "infrx_lab_control;", "  infrx.lab_checkpoint_receipts_of(jsonb), "
       "infrx.lab_withdraw_access_grant(jsonb)\n  to infrx_lab_control;", GRANTS,
       "the provider's login withdraws a customer's grant"),
    # --- AP-07: the revoke-only door
    _s("ap0066_withdraw_by_anyone", "    perform infrx.refuse('forbidden', 'only the owner of the "
       "grantor organization writes');\n  end if;\n  perform pg_advisory_xact_lock(",
       "    null;\n  end if;\n  perform pg_advisory_xact_lock(", WITHDRAWAL,
       "a provider developer withdraws a customer's grant"),
    _s("ap0066_withdraw_twice", "  if prev.grant_id is null or prev.revoked_at is not null then\n"
       "    perform infrx.refuse('state_conflict', 'no current grant to withdraw');",
       "  if prev.grant_id is null then\n"
       "    perform infrx.refuse('state_conflict', 'no current grant to withdraw');",
       WITHDRAWAL, "a repeated withdrawal writes another version (history noise, a 200)"),
    _s("ap0066_withdraw_not_revoked", "    prev.expires_at, infrx.now(), (p_args->>'actor_user_id')"
       "::uuid)\n  returning * into g;\n  return infrx.lab_grant_json(g);\nend $$;\n\n-- ====",
       "    prev.expires_at, null, (p_args->>'actor_user_id')::uuid)\n  returning * into g;\n"
       "  return infrx.lab_grant_json(g);\nend $$;\n\n-- ====", WITHDRAWAL,
       "a withdrawal re-states the grant: sharing continues"),
)


def kill(mutant) -> tuple[str, str]:
    import sys
    return d7.kill(mutant, DB_MUT, sys.modules[__name__])


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    if _reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {_reason}")
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_0066_checks(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    found = _d.superseded(SQL_MUTANTS)
    assert not found, found


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(CHECKS) - {m.check for m in SQL_MUTANTS})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = kill(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
