#!/usr/bin/env python3
"""D10.d item 1-2: the D10 migrations (0019-0021) applied to a copy of the 0018 schema that
carries legacy USD history and CREDIT jobs and holds in every state - counts, exact sums,
identity bindings and permissions compared before and after, the set re-applied, the
previous runtime finishing its in-flight jobs on the upgraded schema, and P-22's alias table
priced before and after.

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_upgrade_d10.py
    INFRX_D_TASK=d10 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_upgrade_d10.py
"""
from __future__ import annotations

import hashlib
import json

import pytest
from infrx.contracts.conformance import builders as b
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_content as ck
from . import checks_credit as cc
from . import checks_dispatch as cdp
from . import checks_leases as cl
from . import checks_operations as co
from . import checks_reads as cd
from . import checks_ready as cr
from . import checks_settle as cs
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_upgrade"
D10 = ("0019_", "0020_", "0021_", "0022_", "0023_", "0024_", "0025_")


def _split():
    """(0001-0018 + clock, the D10 files) as `(label, sql)` pairs."""
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    ours = tuple(f for f in everything if f[0].startswith(D10))
    return tuple(f for f in everything if f not in ours), ours


def seed_history(conn) -> dict:
    """The 0018 world with history: USD grants, a settled USD job with a result, a USD job
    in flight; CREDIT jobs settled, held-unknown, queued and preparing with media attached
    (the previous runtime's `job_media`); the hosted USD rows."""
    ca.seed_admission(conn)
    cd.seed_hosted_usd(conn)
    world = ca.World(conn)
    made = {}
    request, lease = cl.running(conn, world, "up-usd")
    ref = cs.stored(conn, request.request_id)
    assert cs.settle(conn, lease, cs.propose(request.request_id, usage=(1000, 100), ref=ref)
                     )[0] is None
    made["usd_settled"] = request.request_id
    usd_live = b.request(world)
    ca.admit(conn, usd_live, b.idem(usd_live, "up-usd-live"))
    made["usd_preparing"] = usd_live.request_id
    request, lease = cl.credit_running(conn, world, "up-credit")
    ref = cs.stored(conn, request.request_id)
    assert cs.settle(conn, lease, cs.propose(request.request_id, usage=(1200, 340), ref=ref),
                     "credit")[0] is None
    made["credit_settled"] = request.request_id
    made["credit_unknown"] = co.unknown_job(conn, world, "up-unknown").request_id
    made["usd_queued"] = cl.queued(conn, world).request_id
    preparing = ca.credit_request(world, ca.C1_KEY, cc.personal_org(conn, cc.CONSUMER_1))
    ca.admit(conn, preparing, b.idem(preparing, "up-credit-prep"), regime="credit")
    made["credit_preparing"] = preparing.request_id
    return made


_TABLES = """select n.nspname || '.' || c.relname from pg_class c
             join pg_namespace n on n.oid = c.relnamespace
             where n.nspname in ('public', 'infrx') and c.relkind = 'r' order by 1"""


def snapshot(conn) -> dict:
    """Everything the upgrade must preserve, as comparable values."""
    tables = [t for t, in conn.execute(_TABLES).fetchall()]
    counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
    sums = conn.execute("""select
        (select coalesce(sum(ledger_total), 0)::text || '/' || coalesce(sum(reserved_total), 0)
           from infrx.wallets),
        (select coalesce(sum(ledger_total), 0)::text || '/' || coalesce(sum(reserved_total), 0)
           from infrx.credit_wallets),
        (select coalesce(sum(delta_usd), 0)::text from public.credit_ledger),
        (select coalesce(sum(amount), 0)::text from infrx.credit_ledger),
        (select coalesce(sum(amount), 0)::text || '/' || count(*) from infrx.credit_holds
          where state in ('held', 'unknown')),
        (select coalesce(sum(amount), 0)::text || '/' || count(*)
           from infrx.credit_wallet_holds where state in ('held', 'unknown')),
        (select coalesce(sum(cost_usd), 0)::text from public.usage_events)""").fetchone()
    jobs = conn.execute("""select request_id, org_id, key_id, wallet_id, accounting_regime,
        model_revision, requested_model, model_id, deployment_revision_id, serving_version_id,
        rate_card_version, policy_version, price_version, price_snapshot, maximum_hold, state,
        outcome_cause, settlement_state, debit, result_ref, result_expires_at, settled_at,
        idem_payload_hash, payload_digest, request_record from infrx.jobs
        order by request_id""").fetchall()
    acl = conn.execute("""select n.nspname || '.' || c.relname, coalesce(c.relacl::text, ''),
        c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname in ('public', 'infrx') and c.relkind in ('r', 'v') order by 1""").fetchall()
    cols = conn.execute("""select attrelid::regclass::text || '.' || attname,
        attacl::text from pg_attribute where attacl is not null and attrelid in (
          select c.oid from pg_class c join pg_namespace n on n.oid = c.relnamespace
          where n.nspname in ('public', 'infrx')) order by 1""").fetchall()
    fns = conn.execute("""select p.oid::regprocedure::text, coalesce(p.proacl::text, ''),
        p.prosecdef from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname in ('public', 'infrx') order by 1""").fetchall()
    return {"counts": counts, "sums": sums,
            "jobs": hashlib.sha256(json.dumps(jobs, default=str).encode()).hexdigest(),
            "acl": dict((r[0], (_others(r[1]), r[2])) for r in acl),
            "cols": {k: _others(v) for k, v in cols if _others(v) != "{}"},
            "fns": dict((r[0], (_others(r[1]), r[2])) for r in fns)}


def _others(acl: str) -> str:
    """An ACL without the two new logins' entries (0021 grants them their own lists, which
    `checks_reads.check_reads_privileges` asserts): what every OTHER principal holds."""
    items = [i for i in acl.strip("{}").split(",")
             if i and not i.startswith(("infrx_runtime=", "infrx_monitor="))]
    return "{" + ",".join(items) + "}"


def test_d10_upgrade_preserves_history_money_identity_and_grants() -> None:
    base, ours = _split()
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, base)
    conn = pgharness.connect(DB)
    made = seed_history(conn)
    # P-22 before: both priced spellings admitted on 0018 (the literal rows)
    world = ca.World(conn)
    before_prices, in_flight = {}, {}
    for spelling in cd.PRICED:
        request = b.request(world, model_revision=spelling)
        ca.admit(conn, request, b.idem(request, f"p22-before-{spelling}"))
        before_prices[spelling] = cl.row(conn, request.request_id)["price_version"]
        in_flight[spelling] = request
    assert before_prices == {"nemostation/marlin-2b": "pv_marlin2b_usd_2026_09",
                             "nemostation/marlin-2b@2026-09-01": "pv_marlin2b_usd_2026_09_r1"}
    before = snapshot(conn)
    pgharness.apply(DB, ours)
    after = snapshot(conn)
    new_tables = {"infrx.content_objects", "infrx.job_readiness"}
    assert set(after["counts"]) - set(before["counts"]) == new_tables, \
        set(after["counts"]) ^ set(before["counts"])
    assert {t: n for t, n in after["counts"].items() if t not in new_tables} == \
        before["counts"], "the upgrade changed a row count"
    assert after["counts"]["infrx.job_readiness"] == 0, "the upgrade invented a marker"
    assert after["sums"] == before["sums"], (before["sums"], after["sums"])
    assert after["jobs"] == before["jobs"], "the upgrade changed a job's identity or money"
    # permissions: only the declared changes
    changed_acl = {k for k in before["acl"] if after["acl"][k] != before["acl"][k]}
    assert changed_acl == {"infrx.media_uploads"}, changed_acl
    assert "service_role=r/" in after["acl"]["infrx.media_uploads"][0], after["acl"]
    assert {k: v for k, v in after["cols"].items() if k in before["cols"]} == before["cols"]
    redefined_acl = {k for k in before["fns"] if k in after["fns"]
                     and after["fns"][k] != before["fns"][k]}
    assert redefined_acl == set(), f"a redefined function's grants changed: {redefined_acl}"
    # re-applied: nothing moves
    pgharness.apply(DB, ours)
    assert snapshot(conn) == after, "the D10 set is not re-runnable"
    # the previous runtime finishes its in-flight jobs on the upgraded schema
    for key in ("usd_preparing", "credit_preparing"):
        code, answer = cdp.claim(conn, made[key], worker="old-worker")
        assert code is None, f"the previous runtime could not prepare {key}: {code}"
        assert cdp.prepare(conn, answer["lease"])[0] is None
    cs.assert_no_drift(conn, "after the D10 upgrade")
    # CREDIT-CUTOVER, USD in flight: a key admitted before the upgrade replays its OWN job
    # and snapshot after it (never re-priced at the canonical row)
    for spelling, request in in_flight.items():
        again = ca.admit(conn, request, b.idem(request, f"p22-before-{spelling}"))
        assert again["replayed"] is True and again["request_id"] == request.request_id and \
            again["price_snapshot"]["price_version"] == before_prices[spelling], again
    # P-22 after: both spellings price the canonical row; the history is untouched
    for spelling in cd.PRICED:
        request = b.request(world, model_revision=spelling)
        ca.admit(conn, request, b.idem(request, f"p22-after-{spelling}"))
        job = cl.row(conn, request.request_id)
        assert (job["price_version"], job["model_revision"], job["requested_model"]) == (
            "pv_marlin2b_usd_2026_09_r1", cd.CANONICAL, spelling), spelling
    # the operator's registration of database content written before 0020, bounded, once
    registered = conn.execute("select infrx.register_existing_database_content(%s)",
                              ('{"limit": 1000, "grace_s": 60}',)).fetchone()[0]
    assert registered >= 4 and conn.execute(
        "select infrx.register_existing_database_content(%s)",
        ('{"limit": 1000, "grace_s": 60}',)).fetchone()[0] == 0
    print(f"upgrade: {len(before['counts'])} tables, sums {after['sums']}, jobs digest "
          f"{after['jobs'][:12]}, acl change = media_uploads platform DML; re-run no-op; "
          f"previous runtime prepared the 2 preparing jobs of {len(made)} seeded states; P-22 {before_prices} -> r1; "
          f"{registered} pre-0020 content rows registered")



def test_a_pre_0018_success_without_expiry_retires_after_the_upgrade() -> None:
    """R116 (review 1-RI-1): a success settled before 0018 has no persisted expiry (0016's
    terminalize never wrote one; D10 never backfills). After 0019-0021 its database content
    registers and retires through claim -> tombstone -> acknowledge_delete (the scrub is an
    UPDATE of that job row), and the journal sweep runs over it: no constraint added by the
    upgrade re-checks such a row."""
    base, ours = _split()
    name = f"{DB}_legacy"
    pgharness.ensure()
    pgharness.recreate(name)
    pgharness.apply(name, base)
    conn = pgharness.connect(name)
    made = seed_history(conn)
    legacy = made["usd_settled"]
    conn.execute("update infrx.jobs set result_expires_at = null where request_id = %s",
                 (legacy,))
    pgharness.apply(name, ours)
    assert conn.execute("select count(*) from pg_constraint where conrelid = "
                        "'infrx.jobs'::regclass and not convalidated").fetchone()[0] == 0
    registered = conn.execute("select infrx.register_existing_database_content(%s)",
                              ('{"limit": 1000, "grace_s": 0}',)).fetchone()[0]
    assert registered >= 2, registered
    conn.execute("select infrx_test.advance(%s)", (2 * 86_400,))
    for key in (f"job_results/{legacy}", f"jobs/{legacy}"):
        content = ck.row_of(conn, key)
        granted = cr.call(conn, "content_claim", ck._claim_args(content, "legacy-sweeper"))
        tombstone = cr.call(conn, "content_tombstone", {"claim": granted})
        done = cr.call(conn, "content_acknowledge_delete", {"tombstone": tombstone})
        assert done["state"] == "deleted", (key, done)
    assert conn.execute("select (select scrubbed_at is not null from infrx.job_results where "
                        "request_id = %s), (select content_scrubbed_at is not null from "
                        "infrx.jobs where request_id = %s)", (legacy, legacy)).fetchone() == \
        (True, True)
    conn.execute("select infrx.expire_journal(%s)", ('{"limit": 1000}',))
    print(f"pre-0018 success {legacy}: registered, claimed, tombstoned, scrubbed; journal "
          f"sweep ran")
