"""D10.c: persisted result expiry, the consumer read surface, P-22's resolved USD price,
the reconcile race and the runtime role (0021) as SQL-level checks.

Same contract as `checks_ready.py`: the "admission" scenario, each check in a transaction it
rolls back (the races commit on their own connections). The consumer reads run as the real
browser principal - `authenticated` with a JWT subject, both claim forms (`checks._jwt`).
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts.conformance import builders as b
from infrx.state.jobstore import domain_error

from . import checks, checks_admission as ca
from . import checks_content as ck
from . import checks_credit as cc
from . import checks_leases as cl
from . import checks_operations as co
from . import checks_ready as cr
from . import checks_settle as cs
from .checks_leases import lockstep, waiting_on_a_lock

# `alias_compatibility.json` (F2C.c 042ea49d, contracts/v2/published/): every spelling found
# in code, fixtures and results -> the canonical revision and USD price identity, or refused.
CANONICAL = "nemostation/marlin-2b@2026-09-01"
PRICED = {"nemostation/marlin-2b": "pv_marlin2b_usd_2026_09_r1",
          "nemostation/marlin-2b@2026-09-01": "pv_marlin2b_usd_2026_09_r1"}
REFUSED = ("nemostation/marlin-2b-dev@2026-09-01", "nemostation/marlin-2b-dev", "marlin2b",
           "NemoStation/Marlin-2B", "marlin-2b@2026-09-01", "nemostation/marlin-2b@tokcost",
           "deepseek-ai/DeepSeek-V4.1-Flash", "Qwen/Qwen3.8-27B", "moonshotai/Kimi-K3")
# The two USD rows the hosted project carries (rollout W7c, W7e), verbatim.
HOSTED_USD_ROWS = (("pv_marlin2b_usd_2026_09", "nemostation/marlin-2b"),
                   ("pv_marlin2b_usd_2026_09_r1", CANONICAL))


def seed_hosted_usd(conn) -> None:
    for version, revision in HOSTED_USD_ROWS:
        conn.execute("insert into infrx.price_versions (price_version, model_revision, "
                     "input_rate_per_million, output_rate_per_million, token_rules_version, "
                     "effective_from, captured_at) values (%s, %s, 0.10, 0.30, 'tr-1', "
                     "infrx.now(), infrx.now()) "
                     "on conflict (price_version) do nothing", (version, revision))


def as_user(conn, user: str | None, sql: str, params=None):
    """(code, rows) of `sql` as the browser principal `user` (None: anon), rolled back."""
    try:
        with conn.transaction():
            conn.execute(checks._jwt(user) if user else "set local role anon")
            rows = conn.execute(sql, params).fetchall()
            raise _Answer(rows)
    except _Answer as answer:
        return None, answer.rows
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        return getattr(mapped, "code", failed.sqlstate), None


class _Answer(Exception):
    def __init__(self, rows):
        self.rows = rows


# --------------------------------------------------------------------- checks
def check_result_expiry_persisted(conn) -> str:
    """RESULT-EXPIRY / RV-11: the outcome every read gets (status, owned read, idempotent
    replay) carries the `result_expires_at` the SETTLEMENT persisted from ITS configuration;
    the owner's result is served while `now < result_expires_at` and is `result_expired`
    from that instant exactly - whatever today's configuration says - while the terminal
    metadata stays readable and a replay answers it, never a regenerated result."""
    world = ca.World(conn)

    def body():
        request, ref = ck._settled_with_result(conn, world, result_ttl_s=600.0)
        job = cl.row(conn, request.request_id)
        expires = job["settled_at"] + timedelta(seconds=600)
        doc = conn.execute("select infrx.job_admission(%s)", (request.request_id,)
                           ).fetchone()[0]
        assert doc["outcome"].get("result_expires_at") is not None, doc["outcome"]
        idem = b.idem(request, request.request_id)
        lookup = cr.call(conn, "idempotency_lookup", {
            "org_id": request.org_id, "idem": idem.model_dump(mode="json"),
            "limits": {"idempotency_ttl_s": 86_400.0}})
        assert lookup["outcome"].get("result_expires_at") == \
            doc["outcome"].get("result_expires_at"), "status and replay disagree on the expiry"
        ck.at(conn, expires - timedelta(microseconds=1))
        assert ck._read(conn, request.org_id, ref)[0] == "ok"
        ck.at(conn, expires)
        assert ck._read(conn, request.org_id, ref) == ("result_expired", None), \
            "a result outlived the expiry its settlement persisted"
        late = cr.call(conn, "idempotency_lookup", {
            "org_id": request.org_id, "idem": idem.model_dump(mode="json"),
            "limits": {"idempotency_ttl_s": 86_400.0}})
        assert late["replayed"] and late["outcome"]["state"] == "succeeded" and \
            late["outcome"]["result_ref"] == ref, "the terminal metadata was not readable"
        # every new success carries its expiry (0021's NOT VALID check: new rows only)
        running, _lease = cl.running(conn, world, "no-expiry")
        why = cc.attempt(conn, "update infrx.jobs set state = 'succeeded', outcome_cause = "
                               "'completed', settlement_state = 'settled', usage_certainty = "
                               "'authoritative', result_ref = 'infrx-result:' || request_id, "
                               "settled_at = infrx.now() where request_id = %s",
                         (running.request_id,))
        assert why is not None and "jobs_success_has_result_expiry" in why, why
        return "persisted expiry exact in reads and replay; metadata survives content"
    return ca._in_rollback(conn, body)


def check_consumer_reads(conn) -> str:
    """C0/U4 (DUR-RLS): the signed-in individual reads exactly their own jobs - through the
    wallet's personal organization, never a caller-named tenant - newest first, keyset pages
    with no loss or repeat across IDENTICAL timestamps (the frozen clock admits every job at
    one instant), money as exact text in the job's own unit, availability by the persisted
    expiry; another individual's job id answers nothing and its result `not_found`; anon and
    a subject-less session are refused."""
    world = ca.World(conn)

    def body():
        mine = [cr.credit_request(conn, world) for _ in range(5)]
        for n, request in enumerate(mine):
            cr.call(conn, "admit_ready", cr.ready_args(request, b.idem(request, f"read-{n}")))
        settled, ref = ck._settled_with_result(conn, world, result_ttl_s=600.0)
        theirs = ca.credit_request(world, ca.C2_KEY, cc.personal_org(conn, cc.CONSUMER_2))
        ca.admit(conn, theirs, b.idem(theirs, "theirs"), regime="credit")
        me = cc.CONSUMER_1
        seen, after = [], None
        while True:
            code, rows = as_user(conn, me, "select request_id::text, cursor, unit, hold, "
                                 "result_available from public.consumer_jobs(%s, 2)", (after,))
            assert code is None, code
            seen += [row[0] for row in rows]
            if len(rows) < 2:
                break
            after = rows[-1][1]
        expected = {r.request_id for r in mine} | {settled.request_id}
        assert expected <= set(seen) and len(seen) == len(set(seen)), "a page lost or repeated"
        assert theirs.request_id not in seen, "another individual's job was listed"
        code, rows = as_user(conn, me, "select unit, hold, charged, result_available, "
                             "result_expires_at from public.consumer_jobs(null, 10, %s)",
                             (settled.request_id,))
        ((unit, hold, charged, available, expires),) = rows
        assert unit == "CREDIT" and "." in hold and len(hold.split(".")[1]) == 8 and \
            charged is not None and available is True, rows
        code, rows = as_user(conn, me, "select public.consumer_job_result(%s)",
                             (settled.request_id,))
        assert rows == [(f"result of {settled.request_id}",)], (code, rows)
        other = cc.CONSUMER_2
        assert as_user(conn, other, "select request_id from public.consumer_jobs(null, 10, %s)",
                       (settled.request_id,)) == (None, []), "a guessed id answered"
        assert as_user(conn, other, "select public.consumer_job_result(%s)",
                       (settled.request_id,))[0] == "not_found"
        ck.at(conn, expires)
        code, rows = as_user(conn, me, "select result_available from "
                             "public.consumer_jobs(null, 10, %s)", (settled.request_id,))
        assert rows == [(False,)], "the page still offered an expired result"
        assert as_user(conn, me, "select public.consumer_job_result(%s)",
                       (settled.request_id,))[0] == "result_expired"
        assert as_user(conn, None, "select * from public.consumer_jobs()")[0] == "42501"
        assert as_user(conn, str(uuid.uuid4()), "select * from public.consumer_jobs()") == \
            (None, []), "an individual with no wallet saw jobs"
        assert as_user(conn, me, "select * from public.consumer_jobs('x|y')")[0] == \
            "invalid_cursor"
        return f"{len(seen)} own jobs paged by 2 over one timestamp; isolation and expiry hold"
    return ca._in_rollback(conn, body)


def check_usd_resolution(conn) -> str:
    """P-22 (F2C.c `alias_compatibility.json`): a legacy USD admission resolves the model
    string through the catalog listing first and prices the CANONICAL revision - both
    spellings the pilot priced pin `pv_marlin2b_usd_2026_09_r1` at 0.10/0.30 tr-1 with the
    caller's string kept verbatim in `requested_model` - every other spelling is refused, a
    pre-catalog model (the conformance world's) still prices by its own row, and no
    existing price row or job changes. CREDIT pins the listing's card for both spellings."""
    world = ca.World(conn)

    def body():
        seed_hosted_usd(conn)
        prices_before = conn.execute("select * from infrx.price_versions order by 1").fetchall()
        for spelling, price_version in PRICED.items():
            request = b.request(world, model_revision=spelling)
            doc = ca.admit(conn, request, b.idem(request, f"usd-{spelling}"))
            job = cl.row(conn, request.request_id)
            assert (job["model_revision"], job["price_version"], job["requested_model"]) == \
                (CANONICAL, price_version, spelling), (spelling, job["model_revision"],
                                                       job["price_version"])
            assert doc["price_snapshot"]["model_revision"] == CANONICAL
            # G7 WR-3a: discovery reads the same row admission captured
            shown = conn.execute("select infrx.usd_price(%s)", (spelling,)).fetchone()[0]
            assert shown is not None and (shown["price_version"], shown["model_revision"]) \
                == (price_version, CANONICAL), (spelling, shown)
            credit = cr.credit_request(conn, world)
            credit = credit.model_copy(update={"model_revision": spelling})
            ca.admit(conn, credit, b.idem(credit, f"credit-{spelling}"), regime="credit")
            pinned = cl.row(conn, credit.request_id)
            assert (pinned["model_revision"], pinned["rate_card_version"],
                    pinned["requested_model"]) == (CANONICAL, cc.CARD, spelling), spelling
        for spelling in REFUSED:
            request = b.request(world, model_revision=spelling)
            assert ca.refusal(conn, request, b.idem(request, f"refused-{spelling}")) \
                is not None, f"{spelling} was admitted"
            assert conn.execute("select infrx.usd_price(%s)", (spelling,)).fetchone()[0] \
                is None, f"discovery shows a price for {spelling}"
        # a model no listing names keeps pricing by its own literal row (the pilot's
        # pre-catalog path, and the v1 conformance world's)
        conn.execute("insert into infrx.price_versions (price_version, model_revision, "
                     "input_rate_per_million, output_rate_per_million, token_rules_version, "
                     "effective_from, captured_at) values ('pv_pre_catalog', "
                     "'acme/pre-catalog', 0.10, 0.30, 'tr-1', infrx.now(), infrx.now())")
        prices_before = conn.execute("select * from infrx.price_versions order by 1").fetchall()
        plain = b.request(world, model_revision="acme/pre-catalog")
        ca.admit(conn, plain, b.idem(plain, "pre-catalog"))
        job = cl.row(conn, plain.request_id)
        assert (job["price_version"], job["model_revision"], job["requested_model"]) == \
            ("pv_pre_catalog", "acme/pre-catalog", "acme/pre-catalog"), job["price_version"]
        assert conn.execute("select * from infrx.price_versions order by 1").fetchall() == \
            prices_before, "a price row was rewritten"
        return f"{len(PRICED)} spellings priced canonically, {len(REFUSED)} refused"
    return ca._in_rollback(conn, body)


def check_listing_card_authority(conn) -> str:
    """S3 F11 / F2C.c: a card minted for the public deployment but named by no listing never
    reprices it - admission keeps the listing's card - until a NEW listing version names the
    new card; the job admitted before keeps its pinned card."""
    world = ca.World(conn)

    def body():
        conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                     "deployment_revision_id, serving_version_id, input_rate_per_million, "
                     "output_rate_per_million, effective_at, approved_by, provisional) "
                     "select 'rc_minted_not_listed', model_id, deployment_revision_id, "
                     "serving_version_id, 999, 999, infrx.now(), 'ops', true "
                     "from infrx.rate_card_versions where rate_card_version = %s", (cc.CARD,))
        first = cr.credit_request(conn, world)
        ca.admit(conn, first, b.idem(first, "card-1"), regime="credit")
        assert cl.row(conn, first.request_id)["rate_card_version"] == cc.CARD, \
            "a minted card repriced the listing"
        cs.publish_card(conn, "rc_published_next", ("1.00000000", "2.00000000"))
        second = cr.credit_request(conn, world)
        ca.admit(conn, second, b.idem(second, "card-2"), regime="credit")
        assert cl.row(conn, second.request_id)["rate_card_version"] == "rc_published_next"
        assert cl.row(conn, first.request_id)["rate_card_version"] == cc.CARD
        return "the listing's card is the one authority; a new listing version moves it"
    return ca._in_rollback(conn, body)


def check_reconcile_race(connect, database: str) -> str:
    """CREDIT-CUTOVER: the SAME reconcile operation id from two connections - for one request
    the second waits and REPLAYS the audited answer; for another request it waits and is
    `idempotency_conflict` - exactly one audit row, never a raw 23505."""
    owner = connect(database)
    world = ca.World(owner)
    report = []
    for same in (True, False):
        first = co.unknown_job(owner, world, f"wr{same}")
        second = first if same else co.unknown_job(owner, world, f"wr{same}b")
        co.advance(owner, 86_400)
        op = f"race-{uuid.uuid4()}"
        a, bconn = cs.service(connect, database), cs.service(connect, database)
        a.execute("begin")
        got_a = co.reconcile(a, first.org_id, first.request_id, op=op)
        out: dict = {}
        thread = threading.Thread(target=lambda: out.setdefault("b", co.reconcile(
            bconn, second.org_id, second.request_id, op=op)))
        thread.start()
        try:
            waiting_on_a_lock(owner, bconn.info.backend_pid)
        finally:
            a.execute("commit")
            thread.join(10)
        assert got_a[0] is None and got_a[1]["replayed"] is False, got_a
        code, answer = out["b"]
        if same:
            assert code is None and answer["replayed"] is True, out
        else:
            assert code == "idempotency_conflict", f"a racing replay answered {code}"
        audits = owner.execute("select count(*) from infrx.audit_entries where "
                               "idempotency_key = %s", (f"reconcile:{op}",)).fetchone()[0]
        assert audits == 1, f"{audits} audit rows for one operation"
        report.append("replayed" if same else "conflict")
    return "same operation id, two connections: " + ", ".join(report)


def check_flag_freeze_race(connect, database: str) -> str:
    """G8 wiring request 1 (CREDIT-CUTOVER): a regime freeze - the UPDATE of its flag - waits
    for an admission in flight that already passed the flag check (both regimes), so the
    drain after it counts that job; an admission after the freeze is `maintenance`."""
    owner = connect(database)
    world = ca.World(owner)
    report = []
    try:
        for flag, make, kw in (
                ("legacy_usd_admission", lambda: b.request(world), {}),
                ("credit_admission", lambda: cr.credit_request(owner, world),
                 {"regime": "credit"})):
            request = make()
            args = ca.args(request, b.idem(request, f"freeze-{flag}"), **kw)
            first, second = lockstep(owner, (cs.service(connect, database), lambda c: (
                        None, c.execute("select infrx.admit(%s)", (Jsonb(args),)))),
                    (connect(database), lambda c: (None, c.execute(
                        "update infrx.feature_flags set enabled = false, updated_by = 'race', "
                        "reason = 'freeze' where name = %s", (flag,)))))
            assert owner.execute("select state from infrx.jobs where request_id = %s",
                                 (request.request_id,)).fetchone() == ("preparing",), \
                f"{flag}: the admission the freeze waited for is not in flight"
            late = make()
            assert ca.refusal(owner, late, b.idem(late, f"late-{flag}"), **kw) == \
                "dependency_unavailable", f"{flag}: an admission after the freeze"
            report.append(flag)
    finally:
        for flag in ("legacy_usd_admission", "credit_admission"):
            cc.set_flag(owner, flag, True)
    return "a freeze waited for the admission in flight: " + ", ".join(report)


def check_reads_privileges(conn) -> str:
    """DUR-RLS for 0021: the consumer reads are the signed-in principal's only; a browser
    session never writes a key's audience, individual, provider or endpoint; the runtime role
    holds its grant list and no operator operation, money writer, browser table or DDL."""
    for fn, callers in (("public.consumer_jobs(text,integer,uuid)",
                         {"authenticated", "service_role"}),
                        ("public.consumer_job_result(uuid)", {"authenticated", "service_role"}),
                        ("public.consumer_org()", set()),
                        ("infrx.resolve_usd_revision(text)", set()),
                        ("infrx.usd_price(text)", {"service_role", "infrx_runtime"})):
        for role in ("anon", "authenticated", "service_role", "infrx_runtime"):
            allowed, = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                    (role, fn)).fetchone()
            assert allowed == (role in callers), (fn, role)
    for column in ("audience", "user_id", "provider_org_id", "endpoint_id"):
        for verb in ("insert", "update"):
            for role in ("anon", "authenticated"):
                has, = conn.execute("select has_column_privilege(%s, 'public.api_keys', %s, "
                                    "%s)", (role, column, verb)).fetchone()
                assert not has, f"{role} may {verb} api_keys.{column}"
    why = cc.attempt(conn, "update public.api_keys set audience = 'operator' where id = %s",
                     (ca.C1_KEY,), session="owner")
    assert why is not None and why.startswith("42501"), why
    runtime = {"infrx.admit_ready(jsonb)", "infrx.claim_preparation_ready(jsonb)",
               "infrx.terminalize(jsonb)", "infrx.append(jsonb)", "infrx.read_result(uuid,text)",
               "infrx.content_claim(jsonb)"}
    denied = {"infrx.grant_credit(jsonb)", "infrx.reconcile(jsonb)",
              "infrx.set_suspension(jsonb)", "public.claim_signup_grant(uuid,text,uuid)",
              "infrx.register_existing_database_content(jsonb)",
              "infrx.release_aged_unknown(uuid,timestamp with time zone)"}
    for fn in runtime | denied:
        exists = conn.execute("select to_regprocedure(%s) is not null", (fn,)).fetchone()[0]
        if not exists:
            continue
        allowed, = conn.execute("select has_function_privilege('infrx_runtime', %s, "
                                "'execute')", (fn,)).fetchone()
        assert allowed == (fn in runtime), f"infrx_runtime execute {fn}: {allowed}"
    for table, verb in (("public.credit_ledger", "select"), ("public.profiles", "select"),
                        ("infrx.credit_ledger", "insert"), ("infrx.credit_wallets", "update"),
                        ("infrx.jobs", "insert"), ("infrx.jobs", "delete"),
                        ("infrx.audit_entries", "select"), ("infrx.job_results", "select")):
        has, = conn.execute("select has_table_privilege('infrx_runtime', %s, %s)",
                            (table, verb)).fetchone()
        assert not has, f"infrx_runtime may {verb} {table}"
    # I8's privilege_probe identity checks, from the catalog: no attribute that undoes least
    # privilege, no membership, bounds as ROLE defaults (they survive a transaction pooler)
    for role, settings in (("infrx_runtime", {"statement_timeout=15s",
                                              "idle_in_transaction_session_timeout=30s"}),
                           ("infrx_monitor", {"statement_timeout=10s",
                                              "default_transaction_read_only=on"})):
        attributes, members, found = conn.execute(
            "select rolsuper or rolbypassrls or rolcreaterole or rolcreatedb or rolreplication "
            "or rolcanlogin, (select count(*) from pg_auth_members m where m.member = r.oid), "
            "(select array_agg(x) from pg_db_role_setting s, unnest(s.setconfig) x "
            " where s.setrole = r.oid) from pg_roles r where rolname = %s", (role,)).fetchone()
        assert (attributes, members, set(found or ())) == (False, 0, settings), \
            (role, attributes, members, found)
    for table, column, verb in (("infrx.jobs", "state", "select"),
                                ("infrx.outbox", "claimed_at", "select"),
                                ("infrx.jobs", "request_record", "select"),
                                ("infrx.job_results", "body", "select"),
                                ("infrx.credit_holds", "amount", "select"),
                                ("infrx.jobs", "state", "update")):
        has, = conn.execute("select has_column_privilege('infrx_monitor', %s, %s, %s)",
                            (table, column, verb)).fetchone()
        assert has == (column in ("state", "claimed_at") and verb == "select"), \
            f"infrx_monitor {verb} {table}.{column}: {has}"
    return "consumer reads authenticated-only; key scope unwritable; runtime and monitor scoped"


__all__ = ["check_consumer_reads", "check_flag_freeze_race", "check_listing_card_authority", "check_reads_privileges",
           "check_reconcile_race", "check_result_expiry_persisted", "check_usd_resolution",
           "seed_hosted_usd"]
