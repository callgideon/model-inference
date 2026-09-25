"""D10-APP-SQL (0024): the console read port the App lanes asked D10 for, as SQL-level checks.

  - C0 WR-5: `public.consumer_credit_ledger(p_after, p_limit)` - the signed-in individual's
    own CREDIT ledger, keyset-paged on 0006's `credit_ledger_wallet_created_idx` so a page is
    an index range stopped by its LIMIT (O(limit)), not a top-N sort over the wallet.
  - U1R WR-3(b): `credit_ledger_wallet_credits_in_idx` - "Spent" reads only a wallet's
    non-debit entries through the barrier view.
  - U1R WR-3(a): `public.consumer_jobs` gains defaulted `p_model`, `p_key_id`, `p_from`,
    `p_to`; the old three-argument calls answer exactly as before.
  - U4 WR-U4-2: `public.consumer_job_result` refuses (`result_pending`) the success the API
    withholds - usage never reported (`held_unknown`) - after the ownership check.
  - C3A WR-C3A-4: a browser `api_keys` INSERT needs a verified individual (the claim path's
    predicate) who holds a consumer wallet, besides 0001's owner/creator check.

Same contract as `checks_reads.py`: the "admission" scenario, each check in a transaction it
rolls back, the consumer reads as the real browser principal (`checks._jwt`).
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

import psycopg
from infrx.contracts.conformance import builders as b

from . import checks, checks_admission as ca
from . import checks_content as ck
from . import checks_credit as cc
from . import checks_leases as cl
from . import checks_ready as cr
from . import checks_settle as cs
from .checks_reads import _copy_jobs, as_user

LEDGER = "public.consumer_credit_ledger(text,integer)"
KEY_GATE = "public.consumer_may_create_key()"
JOBS = ("public.consumer_jobs(text,integer,uuid,text,uuid,timestamp with time zone,"
        "timestamp with time zone)")
#: The page cap every consumer read shares (0021's `consumer_jobs` clamps to it).
PAGE_CAP = 100
#: Exactly what a consumer ledger row exposes: no wallet, org or actor (R59-2), money as text.
LEDGER_RESULT = ("TABLE(entry_id uuid, created_at timestamp with time zone, kind text, "
                 "amount text, unit text, request_id uuid, reason text, cursor text)")


def _entries(conn, wallet: str, rows) -> None:
    """Ledger rows `(amount, seconds_before_T)` in `wallet` through the real ledger trigger,
    T = the store's (frozen) clock, so several share one instant (keyset ties)."""
    for amount, before in rows:
        conn.execute("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
                     "operation_id, actor, reason, created_at) values (%s, 'consumer', "
                     "'operator_adjustment', %s::numeric, gen_random_uuid(), 'ops', 'adj', "
                     "infrx.now() - make_interval(secs => %s))", (wallet, amount, before))


def _volume(conn, wallet: str, rows: int, kind: str = "operator_adjustment") -> None:
    """`rows` more entries (+-1 alternating, or small debits), one second apart, older than
    anything `_entries` wrote. Debits are written with triggers and FKs off (replica): they
    name no job and do not move the wallet total, which no check here reads."""
    debit = kind == "inference_debit"
    if debit:     # debits name jobs (FK); for a plan only their shape matters - no job rows
        conn.execute("set local session_replication_role = replica")
    conn.execute("""
        insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,
                                         request_id, actor, created_at)
        select %s, 'consumer', %s,
               case when %s then -0.00000001 when i %% 2 = 1 then 1 else -1 end,
               gen_random_uuid(), case when %s then gen_random_uuid() end, 'ops',
               infrx.now() - make_interval(secs => 1000 + i)
        from generate_series(1, %s) as g(i)""", (wallet, kind, debit, debit, rows))
    conn.execute("set local session_replication_role = origin")


def _page_all(conn, user: str, limit: int) -> list[tuple]:
    seen, after = [], None
    while True:
        code, rows = as_user(conn, user, "select entry_id::text, amount, unit, cursor from "
                             "public.consumer_credit_ledger(%s, %s)", (after, limit))
        assert code is None, code
        seen += rows
        if len(rows) < limit:
            return seen
        after = rows[-1][3]


def check_consumer_credit_ledger(conn) -> str:
    """C0 WR-5: exactly the caller's wallet rows, in (created_at desc, entry_id desc) order,
    no loss or repeat across a page boundary inside a run of IDENTICAL timestamps, amounts
    as exact 8-place text that sum to the wallet's total, a bounded page (an over-cap or
    non-positive limit is refused, never clamped), a forged cursor refused; a second
    individual sees only their own rows; anon, a subject-less session, and a subject with
    no wallet see nothing."""
    def body():
        w1, w2 = cc.wallet_of(conn, cc.CONSUMER_1), cc.wallet_of(conn, cc.CONSUMER_2)
        assert w1 and w2, "the admission scenario grants both consumers"
        _entries(conn, w1, [("0.00000001", 0), ("123.45678901", 0), ("-0.12345678", 0),
                            ("7", 0), ("-1.5", 0), ("2.25", 5), ("-0.00000001", 9)])
        _entries(conn, w2, [("3.33333333", 0), ("-1", 2)])
        expected = conn.execute("select entry_id::text, amount::text from infrx.credit_ledger "
                                "where wallet_id = %s order by created_at desc, entry_id desc",
                                (w1,)).fetchall()
        total, = conn.execute("select ledger_total from infrx.credit_wallets "
                              "where wallet_id = %s", (w1,)).fetchone()
        seen = _page_all(conn, cc.CONSUMER_1, 3)
        assert [(r[0], r[1]) for r in seen] == expected, \
            f"pages differ from the wallet's own ordered rows:\n{seen}\n{expected}"
        assert all(isinstance(r[1], str) and re.fullmatch(r"-?\d+\.\d{8}", r[1]) and
                   r[2] == "CREDIT" for r in seen), "an amount left as a number or unlabelled"
        assert sum(Decimal(r[1]) for r in seen) == total, "the page amounts do not sum exactly"
        theirs = {i for i, in conn.execute("select entry_id::text from infrx.credit_ledger "
                                           "where wallet_id = %s", (w2,))}
        other = _page_all(conn, cc.CONSUMER_2, 100)
        assert {r[0] for r in other} == theirs and not theirs & {r[0] for r in seen}, \
            "an individual read another wallet's ledger"
        result, = conn.execute("select pg_get_function_result(%s::regprocedure)",
                               (LEDGER,)).fetchone()
        assert result == LEDGER_RESULT, f"the ledger read exposes {result}"
        for bad in (PAGE_CAP + 1, 1000, 0, -1, None):
            code, rows = as_user(conn, cc.CONSUMER_1, "select entry_id from "
                                 "public.consumer_credit_ledger(null, %s)", (bad,))
            assert code == "invalid_request", f"p_limit {bad}: {code} {len(rows or ())} rows"
        code, rows = as_user(conn, cc.CONSUMER_1, "select entry_id from "
                             "public.consumer_credit_ledger(null, %s)", (PAGE_CAP,))
        assert code is None and len(rows) == len(expected), (code, rows)
        assert as_user(conn, cc.CONSUMER_1, "select * from "
                       "public.consumer_credit_ledger('x|y')")[0] == "invalid_cursor"
        assert as_user(conn, None, "select * from public.consumer_credit_ledger()")[0] == \
            "42501", "anon executed the ledger read"
        assert as_user(conn, str(uuid.uuid4()), "select * from "
                       "public.consumer_credit_ledger()") == (None, []), \
            "an individual with no wallet saw ledger rows"
        try:
            with conn.transaction():
                conn.execute("set local role authenticated")
                conn.execute("select * from public.consumer_credit_ledger()")
                raise AssertionError("a subject-less session read a ledger")
        except psycopg.Error as refused:
            assert refused.sqlstate == "42501", refused.sqlstate
        return (f"{len(seen)} own entries paged by 3 over one instant, exact sum {total}; "
                f"{len(other)} for the second individual, disjoint")
    return ca._in_rollback(conn, body)


def _nested_plans(conn, user: str, sql: str, params=None) -> list[str]:
    """The plans of the statements `sql` runs inside functions, as the browser principal:
    auto_explain at NOTICE, loaded by the owner session first (its settings are SUSET)."""
    got: list[str] = []

    def keep(diag):
        if "plan:" in (diag.message_primary or ""):
            got.append(diag.message_primary)
    conn.execute("load 'auto_explain'")
    conn.add_notice_handler(keep)
    try:
        with conn.transaction():
            for setting, value in (("log_min_duration", "0"), ("log_nested_statements", "on"),
                                   ("log_analyze", "on"), ("log_timing", "off"),
                                   ("log_level", "notice")):
                conn.execute(f"set local auto_explain.{setting} = {value}")
            conn.execute(checks._jwt(user))
            conn.execute(sql, params).fetchall()
            conn.execute("reset role")
            conn.execute("set local auto_explain.log_min_duration = -1")
    finally:
        conn.remove_notice_handler(keep)
    return got


def check_ledger_page_plan(conn) -> str:
    """C0 WR-5's point: a page reads `limit` index entries, whatever the wallet's size.
    With both wallets at 3,000 entries (analyzed), the first page and a page from a cursor
    deep in the history each run as one ordered range scan of
    `credit_ledger_wallet_created_idx` returning exactly `limit` rows: no Sort, no Seq Scan,
    no bitmap (the view's plan: bitmap the wallet, then top-N)."""
    def body():
        w1, w2 = cc.wallet_of(conn, cc.CONSUMER_1), cc.wallet_of(conn, cc.CONSUMER_2)
        for wallet in (w1, w2):
            _volume(conn, wallet, 3000)
        conn.execute("analyze infrx.credit_ledger")
        deep, = conn.execute("select created_at::text || '|' || entry_id::text from "
                             "infrx.credit_ledger where wallet_id = %s order by created_at "
                             "desc, entry_id desc offset 2000 limit 1", (w1,)).fetchone()
        report = []
        for label, after in (("first page", None), ("page after entry 2000", deep)):
            plans = _nested_plans(conn, cc.CONSUMER_1, "select * from "
                                  "public.consumer_credit_ledger(%s, 25)", (after,))
            page = [p for p in plans if "from infrx.credit_ledger l" in p]
            assert len(page) == 1, f"{label}: {len(page)} ledger plans:\n" + "\n".join(plans)
            plan = page[0]
            scan = re.search(r"Index Scan using credit_ledger_wallet_created_idx on "
                             r"credit_ledger l .*\(actual rows=(\d+) loops=1\)", plan)
            assert scan and int(scan.group(1)) == 25 and not re.search(
                r"Sort|Seq Scan|Bitmap", plan), f"{label} is not a LIMIT-bounded range:\n{plan}"
            report.append(f"{label}: index range, {scan.group(1)} rows read")
        return "; ".join(report) + " (3,000 entries per wallet)"
    return ca._in_rollback(conn, body)


def check_credits_in_index(conn) -> str:
    """U1R WR-3(b): the exact read behind "Spent" (U1R `credit-reads.ts`: the barrier view,
    the wallet, `kind <> 'inference_debit'`, bounded), as the signed-in individual, is served
    by the partial index `credit_ledger_wallet_credits_in_idx` (seqscan off, as U1R's P02) -
    it no longer filters the wallet's debits by kind - and answers the same rows."""
    def body():
        w1 = cc.wallet_of(conn, cc.CONSUMER_1)
        _volume(conn, w1, 2000, "inference_debit")
        conn.execute("analyze infrx.credit_ledger")
        sql = ("select amount from public.console_credit_ledger where wallet_id = %s "
               "and kind <> 'inference_debit' limit 101")
        with conn.transaction():
            conn.execute(checks._jwt(cc.CONSUMER_1))
            conn.execute("set local enable_seqscan = off")
            plan = "\n".join(r for r, in conn.execute(f"explain (costs off) {sql}", (w1,)))
            rows = conn.execute(sql, (w1,)).fetchall()
            conn.execute("reset role")
        assert "credit_ledger_wallet_credits_in_idx" in plan, \
            f"credits-in filters every wallet entry by kind:\n{plan}"
        expected, = conn.execute("select count(*) from infrx.credit_ledger where wallet_id = "
                                 "%s and kind <> 'inference_debit'", (w1,)).fetchone()
        assert len(rows) == expected and all(isinstance(a, str) for a, in rows), rows
        return f"credits-in on the partial index ({expected} of {expected + 2000} entries)"
    return ca._in_rollback(conn, body)


def check_consumer_jobs_filters(conn) -> str:
    """U1R WR-3(a): each filter narrows to exactly the matching own jobs (model by the
    requested string or the canonical revision, key, half-open [from, to) window), filters
    combine and page, and every call without them answers what 0021's did (the same rows
    and order as the base table's own-org keyset); a filter never reaches another
    individual's jobs."""
    world = ca.World(conn)

    def body():
        template = cr.credit_request(conn, world)
        cr.call(conn, "admit_ready", cr.ready_args(template, b.idem(template, "filters")))
        org = cc.personal_org(conn, cc.CONSUMER_1)
        key2 = str(uuid.uuid4())
        conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, "
                     "key_hash, audience) values (%s, %s, %s, 'k2', 'sk-infrx-credit1', %s, "
                     "'consumer')", (key2, org, cc.CONSUMER_1, f"hash-{key2}"))
        _copy_jobs(conn, template.request_id, 6)
        copies = [r for r, in conn.execute(
            "select request_id::text from infrx.jobs where job_handle like 'job_copy_%%' "
            "and org_id = %s order by job_handle", (org,))]
        assert len(copies) == 6, copies
        conn.execute("alter table infrx.jobs disable trigger user")
        for n, rid in enumerate(copies):
            conn.execute("update infrx.jobs set created_at = infrx.now() - make_interval("
                         "hours => %s), key_id = case when %s then %s::uuid else key_id end, "
                         "requested_model = case when %s then 'x/other' else requested_model "
                         "end where request_id = %s", (n + 1, n < 2, key2, n in (2, 3), rid))
        conn.execute("alter table infrx.jobs enable trigger user")
        me = cc.CONSUMER_1
        own = [r for r, in conn.execute(
            "select request_id::text from infrx.jobs where org_id = %s "
            "order by created_at desc, request_id", (org,))]
        cols = "request_id::text, cursor, hold, charged, result_available"
        old = as_user(conn, me, f"select {cols} from public.consumer_jobs(null, 100)")
        named = as_user(conn, me, f"select {cols} from public.consumer_jobs(p_after => null, "
                        "p_limit => 100, p_request_id => null, p_model => null, "
                        "p_key_id => null, p_from => null, p_to => null)")
        assert old[0] is None and old == named and [r[0] for r in old[1]] == own, \
            f"the unfiltered call changed: {old} / {named}"

        def ids(**kw):
            args = ", ".join(f"p_{k} => %s" for k in kw)
            code, rows = as_user(conn, me, f"select request_id::text from "
                                 f"public.consumer_jobs(p_limit => 100, {args})",
                                 tuple(kw.values()))
            assert code is None, (kw, code)
            return [r for r, in rows]
        assert ids(key_id=key2) == copies[:2], "the key filter"
        assert ids(model="x/other") == copies[2:4], "the model filter (requested string)"
        canonical, = conn.execute("select model_revision from infrx.jobs where request_id = "
                                  "%s", (template.request_id,)).fetchone()
        assert ids(model=canonical) == own, "the model filter (canonical revision)"
        assert ids(model="nobody/none") == [], "an unknown model matched"
        window = conn.execute("select infrx.now() - interval '4 hours', "
                              "infrx.now() - interval '2 hours'").fetchone()
        assert ids(**{"from": window[0], "to": window[1]}) == copies[2:4], \
            "the window is not [from, to)"
        cut, = conn.execute("select infrx.now() - interval '90 minutes'").fetchone()
        assert ids(key_id=key2, to=cut) == copies[1:2], "filters do not combine (AND)"
        assert ids(**{"from": window[1]}) == [r for r in own if r not in copies[2:]], \
            "p_from alone"
        paged, after = [], None
        while True:
            code, rows = as_user(conn, me, "select request_id::text, cursor from "
                                 "public.consumer_jobs(p_after => %s, p_limit => 1, "
                                 "p_key_id => %s)", (after, key2))
            assert code is None, code
            paged += [r[0] for r in rows]
            if not rows:
                break
            after = rows[-1][1]
        assert paged == copies[:2], f"a filtered keyset lost or repeated: {paged}"
        assert as_user(conn, cc.CONSUMER_2, "select request_id from public.consumer_jobs("
                       "p_key_id => %s)", (key2,)) == (None, []), \
            "a key filter reached another individual's jobs"
        assert as_user(conn, cc.CONSUMER_2, "select request_id from public.consumer_jobs("
                       "p_model => %s)", ("x/other",)) == (None, [])
        return f"{len(own)} own jobs; key/model/window filters exact, combined and paged"
    return ca._in_rollback(conn, body)


def check_port_privileges(conn) -> str:
    """R59-4 / R122-R127 for 0024: both reads are the signed-in principal's (and the
    platform's), never anon's, the runtime login's or the monitor's; the old
    three-argument `consumer_jobs` signature is gone (one function, so no call is
    ambiguous); both are SECURITY DEFINER with a pinned search_path."""
    for fn in (LEDGER, JOBS, KEY_GATE):
        for role in ("anon", "authenticated", "service_role", "infrx_runtime",
                     "infrx_monitor", "public"):
            if role == "public":
                acl, = conn.execute("select proacl::text from pg_proc where oid = "
                                    "%s::regprocedure", (fn,)).fetchone()
                assert acl is not None and not re.search(r"(^|[{,])=X", acl), (fn, acl)
                continue
            allowed, = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                    (role, fn)).fetchone()
            assert allowed == (role in ("authenticated", "service_role")), (fn, role)
        definer, config = conn.execute("select prosecdef, proconfig::text from pg_proc where "
                                       "oid = %s::regprocedure", (fn,)).fetchone()
        assert definer and config == "{\"search_path=public, infrx, pg_temp\"}", \
            (fn, definer, config)
    old, = conn.execute("select count(*) from pg_proc p join pg_namespace n on "
                        "n.oid = p.pronamespace where n.nspname = 'public' and "
                        "p.proname = 'consumer_jobs'").fetchone()
    assert old == 1, f"{old} consumer_jobs overloads"
    return "0024 reads: authenticated + service_role only; definer, pinned search_path"


def check_result_withheld(conn) -> str:
    """U4 WR-U4-2: the direct RPC serves what the gateway serves. A success whose usage was
    never reported (`held_unknown`, state succeeded, result stored, expiry persisted) is
    refused `result_pending` to its owner - the API withholds it, and an unreconciled hold
    can end released as platform-absorbed, never charged; the settled sibling is served;
    another individual gets `not_found` for both (the ownership check comes first)."""
    world = ca.World(conn)

    def body():
        held, lease = cl.credit_running(conn, world)
        cl.publish(conn, lease)
        code, doc = cs.settle(conn, lease, cs.propose(held.request_id,
                                                      ref=cs.stored(conn, held.request_id)),
                              "credit")
        assert code is None and doc["outcome"]["settlement_state"] == "held_unknown", doc
        job = cl.row(conn, held.request_id)
        assert job["state"] == "succeeded" and job["result_ref"] and \
            job["result_expires_at"] is not None, job
        settled, _ref = ck._settled_with_result(conn, world, result_ttl_s=600.0)
        me, other = cc.CONSUMER_1, cc.CONSUMER_2
        read = "select public.consumer_job_result(%s)"
        got = as_user(conn, me, read, (held.request_id,))
        assert got[0] == "result_pending", f"consumer_job_result served an unknown-usage " \
            f"result: {got}"
        assert as_user(conn, me, read, (settled.request_id,)) == \
            (None, [(f"result of {settled.request_id}",)]), "the settled result is not served"
        for request in (held, settled):
            assert as_user(conn, other, read, (request.request_id,))[0] == "not_found", \
                "another individual's result is not not_found"
        return "held_unknown success refused result_pending; settled served; foreign not_found"
    return ca._in_rollback(conn, body)


def check_key_insert_needs_verified_wallet(conn) -> str:
    """C3A WR-C3A-4: an individual's own-org key INSERT through the browser grant (0001's
    column list, as PostgREST sends it) is accepted only for a verified individual
    (`infrx.verified_user` evidence and a live email, the claim path's predicate) who holds
    a consumer wallet; unverified with or without a wallet, and verified without one, are
    refused by row-level security (42501)."""
    def body():
        # The admission scenario grants through the A1 seam, which takes its evidence as an
        # argument: CONSUMER_1 has a wallet but no `email_confirmed_at` yet.
        def insert(user: str):
            return as_user(conn, user, "insert into public.api_keys (org_id, created_by, "
                           "name, prefix, key_hash) values (%s, %s, 'k', 'sk-infrx-c3a4key0', "
                           "%s) returning id", (cc.personal_org(conn, user), user,
                                                f"hash-c3a4-{user}"))
        refused = {"unverified, funded": insert(cc.CONSUMER_1),
                   "unverified, no wallet": insert(cc.RACER)}
        conn.execute("update auth.users set email_confirmed_at = infrx.now() "
                     "where id in (%s, %s)", (cc.CONSUMER_1, cc.UNGRANTED))
        refused["verified, no wallet"] = insert(cc.UNGRANTED)
        for case, (code, _rows) in refused.items():
            assert code == "42501", f"{case}: an api_keys insert was accepted ({code})"
        code, rows = insert(cc.CONSUMER_1)
        assert code is None and len(rows) == 1, f"a verified, funded individual: {code}"
        return f"refused {sorted(refused)}; a verified individual with a wallet accepted"
    return ca._in_rollback(conn, body)


__all__ = ["check_consumer_credit_ledger", "check_consumer_jobs_filters",
           "check_credits_in_index", "check_key_insert_needs_verified_wallet",
           "check_ledger_page_plan", "check_port_privileges", "check_result_withheld"]
