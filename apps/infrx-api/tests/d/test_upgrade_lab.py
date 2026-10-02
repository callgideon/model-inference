#!/usr/bin/env python3
"""R151 / lab-sql brief item 5: the Lab migrations (0027 on) applied to a 0001-0026 database
that holds consumer history - admitted CREDIT and USD jobs, money, identity and grants - change
nothing that existed (row counts except the declared seed rows, money sums, the jobs' identity
and money, every ACL of an existing relation or function) and are re-runnable.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_upgrade_lab.py
"""
from __future__ import annotations

import psycopg
import pytest
from infrx.state import migrations

from . import pgharness
from . import test_upgrade_d10 as d10

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_upgrade_lab"

#: What each Lab migration adds. A new Lab migration extends these three, nothing else.
NEW_TABLES = {"infrx.lab_access_grants",                                    # 0027
              *(f"infrx.{t}" for t in ("lab_sources", "lab_records",          # 0029
                                       "lab_dataset_samples", "lab_eval_runs",
                                       "lab_eval_cases", "lab_eval_attempts",
                                       "lab_eval_results", "lab_checkpoint_receipts",
                                       "lab_outbox")),
              *(f"infrx.{t}" for t in ("lab_budgets", "lab_budget_limits",    # 0031
                                       "lab_submissions", "lab_submission_consents")),
              "infrx.lab_control_events",                                   # 0032
              *(f"infrx.{t}" for t in ("lab_rollouts", "lab_rollout_events",  # 0033
                                       "lab_rollout_assignments")),
              "infrx.lab_evaluators", "infrx.lab_eval_reports",             # 0034
              "infrx.feedback_scrubs",                                      # 0035
              *(f"infrx.{t}" for t in ("lab_judge_runs", "lab_judge_results", # 0036
                                       "lab_judge_audit")),
              "infrx.lab_judge_configs", "infrx.lab_judge_requests",        # 0037
              "infrx.lab_variant_comparisons",                              # 0040
              *(f"infrx.{t}" for t in ("lab_content_refs",                    # 0041
                                       "lab_sample_tombstones", "lab_sample_bounds")),
              *(f"infrx.{t}" for t in ("lab_label_events", "lab_external_runs",  # 0042
                                       "lab_external_run_events", "lab_run_reservations",
                                       "lab_pipeline_notes", "lab_judge_run_consents",
                                       "lab_teacher_failures", "lab_checkpoint_events",
                                       "lab_checkpoint_rejections",
                                       "lab_checkpoint_subscriptions",
                                       "lab_checkpoint_decisions")),
              *(f"infrx.{t}" for t in ("lab_experiments", "lab_release_proposals",  # 0043
                                       "lab_judge_calibrations")),
              "infrx.lab_import_jobs",                                      # 0051
              "infrx.lab_variant_identities",                               # 0058
              "infrx.control_operations", "infrx.control_idempotency",      # 0060 (R271)
              "infrx.lab_judge_cancellations", "infrx.lab_trace_reviews"}   # 0064 (R271)
SEEDED: dict[str, int] = {}                                                 # none yet


def split(everything=None):
    """(the shim, 0001-0026 and the clock; the Lab files 0027 on) as `(label, sql)` pairs."""
    everything = everything or migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    lab = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0027")
    return tuple(f for f in everything if f not in lab), lab


def test_the_split_puts_every_file_from_0027_in_the_lab_set() -> None:
    files = (("shim.sql", ""), ("0026_x.sql", ""), ("0027_a.sql", ""), ("0031_b.sql", ""),
             ("clock.sql", ""))
    base, lab = split(files)
    assert [f for f, _ in base] == ["shim.sql", "0026_x.sql", "clock.sql"]
    assert [f for f, _ in lab] == ["0027_a.sql", "0031_b.sql"]


def test_lab_upgrade_preserves_history_money_identity_and_grants() -> None:
    base, lab = split()
    assert lab, "no Lab migration to upgrade to"
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, base)
    conn = pgharness.connect(DB)
    made = d10.seed_history(conn)
    # a pre-Lab feedback row: D6F's entry order must number it, and it stays as it was
    conn.execute("insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
                 "author_role, channel, name, value_bool) select 'fb_pre_lab', org_id, "
                 "request_id, 'k', 'customer', 'api', 'thumb', true from infrx.jobs "
                 "where request_id = %s", (made["usd_settled"],))
    before = d10.snapshot(conn)
    pgharness.apply(DB, lab)
    after = d10.snapshot(conn)
    assert set(after["counts"]) - set(before["counts"]) == NEW_TABLES, \
        set(after["counts"]) ^ set(before["counts"])
    assert {t: n for t, n in after["counts"].items() if t in NEW_TABLES} == \
        {t: SEEDED.get(t, 0) for t in NEW_TABLES}, "a Lab table was seeded from history"
    assert {t: n for t, n in after["counts"].items() if t not in NEW_TABLES} == \
        before["counts"], "the Lab upgrade changed a row count"
    assert after["sums"] == before["sums"], (before["sums"], after["sums"])
    assert after["jobs"] == before["jobs"], "the Lab upgrade changed a job's identity or money"
    # (0043 WR-I2L-4: the one change - the control login appends to A3's registry, select +
    # insert, as its PgRegistry does; nothing else of an existing relation moves)
    # (0044 WR-LSQ-9: and reads the catalog listings, select only)
    control = {f"infrx.{r}": ",infrx_lab_control=ar/postgres" for r in (
        "model_versions", "serving_versions", "deployment_revisions", "rate_card_versions")}
    control["infrx.catalog_listings"] = ",infrx_lab_control=r/postgres"
    assert all(g in after["acl"][r][0] for r, g in control.items()), \
        {r: after["acl"][r] for r in control}
    kept = {k: ((v[0].replace(control[k], ""), *v[1:]) if k in control else v)
            for k, v in after["acl"].items() if k in before["acl"]}
    assert kept == before["acl"], "the Lab upgrade changed an existing relation's grants"
    assert {k: v for k, v in after["cols"].items() if k in before["cols"]} == before["cols"]
    changed = {k for k in before["fns"] if after["fns"].get(k) != before["fns"][k]}
    # (and executes the clock /readyz reads and the price PgCatalogDirectory reads)
    assert changed == {"infrx.now()", "infrx.usd_price(text)"}, \
        f"an existing function's grants changed: {changed}"
    assert all(after["fns"][k] == (before["fns"][k][0][:-1] + ",infrx_lab_control=X/postgres}",
                                   before["fns"][k][1]) for k in changed), \
        {k: (before["fns"][k], after["fns"][k]) for k in changed}
    assert conn.execute("select entry_seq from infrx.feedback where feedback_id = 'fb_pre_lab'"
                        ).fetchone()[0] is not None                              # 0028
    pgharness.apply(DB, lab)
    assert d10.snapshot(conn) == after, "the Lab set is not re-runnable"
    print(f"Lab upgrade over {len(made)} seeded job states: {len(before['counts'])} tables "
          f"unchanged, +{sorted(NEW_TABLES)}; sums {after['sums']}; re-run no-op")


def test_the_lw2_requests_keep_the_lab_rows_already_written() -> None:
    """0036-0040 (the LW2 schema requests) over a database already holding Lab rows written
    by 0027-0035 - a running rollout, a budget with a hold - leave those rows as they were
    (the new columns empty) and are re-runnable."""
    from . import test_d9_rollout as d9
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    later = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0036")
    assert [f for f, _ in later][:1] == ["0036_lab_judge_ledger.sql"], later
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, tuple(f for f in everything if f not in later))
    conn = pgharness.connect(DB)
    d9.seed(conn)
    ref = d9.t.publish(conn, d9.policy(d9.uid(1, 0xa0)))
    d9.start(conn, ref)
    d9.ok(conn, "lab_put_budget", {"provider_org_id": d9.NEMO, "payer_ref": d9.t.PAYER,
                                   "limit": "10.00000000", "actor": "ops", "reason": "q4"})
    rows = ("select row_to_json(o)::jsonb - 'plan_digest' - 'shadow_limit' "     # 0043's
            "from infrx.lab_rollouts o",
            "select row_to_json(e)::jsonb - 'decision_doc' - 'reasons' "
            "from infrx.lab_rollout_events e",
            "select row_to_json(b)::jsonb from infrx.lab_budgets b")
    before = [conn.execute(q).fetchall() for q in rows]
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows] == before
    assert conn.execute("select plan_digest from infrx.lab_rollouts").fetchall() == [(None,)]
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows] == before, "not re-runnable"


def test_the_lw3_requests_keep_the_lab_rows_already_written() -> None:
    """0041-0043 (C2-RPC, D8 and the remaining LW3 requests) over a database already holding
    Lab rows written by 0027-0040 - a released judge run on a budget, a running rollout, a
    dataset - leave those rows as they were (the judge run is an `external_judging` run with
    no dataset, the rollout's shadow bound 0), and are re-runnable."""
    from . import test_d6j_judge as j
    from . import test_d9_rollout as d9
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    later = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0041")
    assert [f for f, _ in later][:1] == ["0041_lab_content_refs.sql"], later
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, tuple(f for f in everything if f not in later))
    conn = pgharness.connect(DB)
    d9.seed(conn)
    d9.start(conn, d9.t.publish(conn, d9.policy(d9.uid(1, 0xa1))))
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('lab_submission', true, 'upgrade', 'test')")
    j.W["grant_id"], j.W["version"] = (lambda g: (g["grant_id"], g["version"]))(
        j.judge_grant(conn, purposes=["provider_sharing", "training", "external_judging"]))
    j.put_budget(conn, "100.00000000")
    j.ok(conn, "lab_judge_reserve", j.reserve(j.uid(1, 0xa2)))
    j.ok(conn, "lab_judge_begin_submit", {"run_id": j.uid(1, 0xa2)})
    j.ok(conn, "lab_judge_release", {"run_id": j.uid(1, 0xa2), "state": "failed",
                                     "reason": "refused"})
    rows = ("select row_to_json(r)::jsonb from infrx.lab_judge_runs r",
            "select row_to_json(o)::jsonb - 'shadow_limit' from infrx.lab_rollouts o",
            "select row_to_json(b)::jsonb from infrx.lab_budgets b",
            "select count(*) from infrx.lab_dataset_samples")
    before = [conn.execute(q).fetchall() for q in rows]
    pgharness.apply(DB, later)
    after = [conn.execute(q).fetchall() for q in rows]
    judge = [{k: v for k, v in r[0].items() if k not in ("purpose", "dataset_ref")}
             for r in after[0]]
    assert judge == [r[0] for r in before[0]] and after[1:] == before[1:], (before, after)
    assert conn.execute("select purpose, dataset_ref from infrx.lab_judge_runs").fetchall() == \
        [("external_judging", None)]
    assert conn.execute("select shadow_limit from infrx.lab_rollouts").fetchall() == [(0,)]
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows] == after, "not re-runnable"


def test_the_lw4_reads_keep_the_listings_already_written() -> None:
    """0044 (WR-LSQ-9) over a database already holding a published alias (L3-SQL's world at
    0043): the listings stay as they were, the control login reads them, and it re-runs."""
    from . import test_l3sql_control as l3
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    later = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0044")
    assert [f for f, _ in later][:1] == ["0044_lab_control_reads.sql"], later
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, tuple(f for f in everything if f not in later))
    conn = pgharness.connect(DB)
    l3.seed(conn)
    l3.ok(conn, "lab_control_publish", l3.publish())
    rows = "select row_to_json(l)::jsonb from infrx.catalog_listings l order by version"

    def as_control() -> str | list:
        try:
            with conn.transaction():
                conn.execute("set local session authorization infrx_lab_control")
                return conn.execute(rows).fetchall()
        except psycopg.Error as refused:
            return refused.sqlstate
    before = conn.execute(rows).fetchall()
    assert len(before) == 2 and as_control() == "42501", as_control()
    pgharness.apply(DB, later)
    assert conn.execute(rows).fetchall() == before and as_control() == before
    pgharness.apply(DB, later)
    assert as_control() == before, "not re-runnable"


def test_the_lw5_upgrade_keeps_a_running_policy_and_refuses_its_stale_candidate() -> None:
    """0045 (E8L-F1, WR-E8L-2) touches no table, only `release_active`'s body: a policy
    published and started at 0044 (its candidate ref keyed by serving_version_id, the
    pre-fix identity) is untouched by the upgrade, and `release_active` - which never saw
    that ref before 0045 either, since nothing routes to this policy's own endpoint - moves
    from resolving nothing new to refusing the same stale-style ref outright once a listing
    does route to it, rather than silently accepting the wrong identity space."""
    from . import test_d9_rollout as d9
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    later = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0045")
    assert [f for f, _ in later][:1] == ["0045_lab_serving_ref_identity.sql"], later
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, tuple(f for f in everything if f not in later))
    conn = pgharness.connect(DB)
    d9.seed(conn)
    stale = d9.policy(d9.uid(1, 0xb5))
    ref = d9.t.publish(conn, stale)
    d9.start(conn, ref)
    rows = ("select row_to_json(r)::jsonb from infrx.lab_records r order by ref",
            "select row_to_json(o)::jsonb from infrx.lab_rollouts o order by policy_id")
    before = [conn.execute(q).fetchall() for q in rows]
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows] == before, "0045 changed a stored row"
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows] == before, "not re-runnable"


def test_the_lw6_upgrade_keeps_existing_rows_and_reruns() -> None:
    """0047-0051 (E3L-F2, WR-R4-1, WR-LSQ-C2A/B, WR-N4-3) over a database already holding a
    proposed revision (0032's world at 0046): the additive functions and the new
    `lab_import_jobs` table land without touching a single existing row, and every file
    re-runs."""
    from . import test_l3sql_control as l3
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    later = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0047")
    assert [f for f, _ in later][:1] == ["0047_lab_control_propose_idempotent.sql"], later
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, tuple(f for f in everything if f not in later))
    conn = pgharness.connect(DB)
    l3.seed(conn)
    l3.validated(conn)
    proposed = l3.ok(conn, "lab_control_propose", {
        "proposal": l3.proposal(), "source_revision_id": l3.D2, "actor": "admin@nemo"})
    rows = ("select row_to_json(d)::jsonb from infrx.deployment_revisions d order by "
            "deployment_revision_id",
            "select row_to_json(e)::jsonb from infrx.lab_control_events e order by event_id")
    before = [conn.execute(q).fetchall() for q in rows]
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows] == before, "0047-0051 changed a stored row"
    # A retry of the SAME source now answers the open proposal (R205), not a fresh insert:
    retried = l3.ok(conn, "lab_control_propose", {
        "proposal": l3.proposal(l3.RETRY_ID), "source_revision_id": l3.D2,
        "actor": "admin@nemo"})
    assert retried["deployment_revision_id"] == proposed["deployment_revision_id"]
    pgharness.apply(DB, later)
    assert [conn.execute(q).fetchall() for q in rows[:1]] == before[:1], "not re-runnable"
