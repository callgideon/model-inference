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
        # written once: no role moves a settled job's expiry (0021 jobs_result_expiry_guard),
        # neither later nor earlier - an earlier one would make live content deletable
        for session in (None, "service"):
            for shift in ("+ interval '365 days'", "- interval '600 seconds'"):
                why = cc.attempt(conn, f"update infrx.jobs set result_expires_at = "
                                       f"result_expires_at {shift} where request_id = %s",
                                 (request.request_id,), session=session)
                assert why is not None and why.startswith("23514") and "immutable" in why, \
                    f"a settled job's persisted expiry was rewritten ({session}): {why}"
        # R116: no NOT VALID constraint on jobs (it would re-check every later UPDATE of a
        # pre-0018 success and refuse its scrub)
        assert conn.execute("select count(*) from pg_constraint where conrelid = "
                            "'infrx.jobs'::regclass and not convalidated").fetchone()[0] == 0
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
        # a legacy USD job of the same individual is labelled USD, never CREDIT
        own_org = cc.personal_org(conn, me)
        conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                     "values (%s, 25, 'grant', 'fixture')", (own_org,))
        usd = b.request(world, org_id=own_org, key_id=ca.C1_KEY)
        ca.admit(conn, usd, b.idem(usd, "read-usd"))
        code, rows = as_user(conn, me, "select unit, hold, hold_state from "
                             "public.consumer_jobs(null, 10, %s)", (usd.request_id,))
        assert code is None and len(rows) == 1 and rows[0][0] == "USD" and \
            rows[0][1] == cl.row(conn, usd.request_id)["maximum_hold"].__format__(".8f"), \
            f"a USD job was shown as {rows}"
        # a page is at most 100 rows, whatever the caller asks
        _copy_jobs(conn, settled.request_id, 101)
        code, rows = as_user(conn, me, "select request_id from public.consumer_jobs(null, 1000)")
        assert code is None and len(rows) == 100, f"a page of {len(rows or ())} rows"
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


def _copy_jobs(conn, request_id: str, n: int) -> None:
    """`n` copies of one job row (new ids, no idempotency key) - a long history for paging,
    written under the table's own constraints with its row triggers off (rolled back)."""
    cols = [c for c, in conn.execute(
        "select column_name from information_schema.columns where table_schema = 'infrx' "
        "and table_name = 'jobs' and column_name not in ('request_id', 'job_handle', "
        "'idempotency_key', 'idem_payload_hash') and is_generated = 'NEVER'")]
    names = ", ".join(cols)
    conn.execute("alter table infrx.jobs disable trigger user")
    conn.execute(f"insert into infrx.jobs (request_id, job_handle, {names}) "
                 f"select gen_random_uuid(), 'job_copy_' || n, {names} from infrx.jobs, "
                 f"generate_series(1, %s) n where request_id = %s", (n, request_id))
    conn.execute("alter table infrx.jobs enable trigger user")


def _next_listing(conn, label: str, effective: str) -> None:
    """A second serving version (`label`) on its own public active deployment and card,
    listed for the alias as the next listing version at `effective` - catalog rows written
    with their lifecycle triggers off (the registry's own rules are D's other checks)."""
    tables = ("serving_versions", "deployment_revisions", "rate_card_versions",
              "catalog_listings")
    for t in tables:
        conn.execute(f"alter table infrx.{t} disable trigger user")
    serving, = conn.execute(
        "insert into infrx.serving_versions (model_version_id, model_id, provider_org_id, "
        "revision_label, prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
        "engine_options_digest, precision, capability, created_by) select model_version_id, "
        "model_id, provider_org_id, %s, prompt_harness_ref, preprocessor_profile_version, "
        "runtime_image_ref, engine_options_digest, precision, capability, 'o' from "
        "infrx.serving_versions where serving_version_id = %s returning serving_version_id",
        (label, cc.SERVING)).fetchone()
    cols = ", ".join(c for c, in conn.execute(
        "select column_name from information_schema.columns where table_schema = 'infrx' "
        "and table_name = 'deployment_revisions' and column_name not in "
        "('deployment_revision_id', 'serving_version_id') and is_generated = 'NEVER'"))
    deployment, = conn.execute(
        f"insert into infrx.deployment_revisions ({cols}, serving_version_id) select {cols}, "
        f"%s from infrx.deployment_revisions where deployment_revision_id = %s "
        f"returning deployment_revision_id", (serving, cc.PUBLIC_DEPLOYMENT)).fetchone()
    card = f"rc_{label}"
    conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                 "deployment_revision_id, serving_version_id, input_rate_per_million, "
                 "output_rate_per_million, effective_at, approved_by, provisional) values "
                 "(%s, %s, %s, %s, 1, 1, infrx.now(), 'ops', true)",
                 (card, cc.MODEL, deployment, serving))
    conn.execute("insert into infrx.catalog_listings (public_model_id, version, model_id, "
                 "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
                 "approved_by) select 'nemostation/marlin-2b', max(version) + 1, %s, %s, %s, "
                 f"%s, {effective}, 'ops' from infrx.catalog_listings where public_model_id = "
                 "'nemostation/marlin-2b'", (cc.MODEL, deployment, serving, card))
    for t in tables:
        conn.execute(f"alter table infrx.{t} enable trigger user")


def _shown(conn, model: str) -> str | None:
    doc = conn.execute("select infrx.usd_price(%s)", (model,)).fetchone()[0]
    return doc and doc["price_version"]


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
        # discovery prices what admission charges at the edges of "resolve, then price":
        # a retired price row, a future listing, the newest listing, an unserved deployment
        alias = "nemostation/marlin-2b"
        conn.execute("select infrx_test.advance(10)")
        conn.execute("insert into infrx.price_versions (price_version, model_revision, "
                     "input_rate_per_million, output_rate_per_million, token_rules_version, "
                     "effective_from, effective_to, captured_at) values ('pv_retired', %s, 9, "
                     "9, 'tr-1', infrx.now() - interval '5 s', infrx.now() - interval '1 s', "
                     "infrx.now())", (CANONICAL,))
        retired = b.request(world, model_revision=alias)
        ca.admit(conn, retired, b.idem(retired, "edge-retired"))
        charged = cl.row(conn, retired.request_id)["price_version"]
        assert _shown(conn, alias) == charged == PRICED[alias], \
            f"a retired price: discovery {_shown(conn, alias)}, admission {charged}"
        _next_listing(conn, "2026-10-01", "infrx.now() + interval '1 day'")
        conn.execute("insert into infrx.price_versions (price_version, model_revision, "
                     "input_rate_per_million, output_rate_per_million, token_rules_version, "
                     "effective_from, captured_at) values ('pv_next', %s, 1, 1, 'tr-1', "
                     "infrx.now(), infrx.now())", ("nemostation/marlin-2b@2026-10-01",))
        assert _shown(conn, alias) == PRICED[alias], "a future listing was priced"
        conn.execute("select infrx_test.advance(%s)", (86_400,))
        assert _shown(conn, alias) == "pv_next", "an older listing version was priced"
        conn.execute("alter table infrx.deployment_revisions disable trigger user")
        conn.execute("update infrx.deployment_revisions set state = 'draining' where "
                     "serving_version_id = (select serving_version_id from "
                     "infrx.serving_versions where revision_label = '2026-10-01')")
        conn.execute("alter table infrx.deployment_revisions enable trigger user")
        assert _shown(conn, alias) == HOSTED_USD_ROWS[0][0], \
            "a listing whose deployment is not served publicly was priced"
        return f"{len(PRICED)} spellings priced canonically, {len(REFUSED)} refused; 4 edges"
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


#: The functions `infrx_runtime` executes: 0021's grant loop (0021:490-515), plus 0022's
#: `fail_preparation`, minus 0023's two unmarked doors (R123) - the relay admits through
#: `admit_ready` and the preparation worker claims through `claim_preparation_ready` (W5).
REVOKED_DOORS = frozenset({"infrx.admit(jsonb)", "infrx.claim_preparation(jsonb)"})
RUNTIME_FUNCTIONS = frozenset({
    "infrx.acknowledge_dispatch(jsonb)", "infrx.admit_ready(jsonb)", "infrx.append(jsonb)",
    "infrx.cancel(jsonb)", "infrx.claim(jsonb)", "infrx.claim_preparation_ready(jsonb)",
    "infrx.content_acknowledge_delete(jsonb)", "infrx.content_candidates(jsonb)",
    "infrx.content_claim(jsonb)", "infrx.content_references(jsonb)",
    "infrx.content_register(jsonb)", "infrx.content_tombstone(jsonb)",
    "infrx.dispatch_pending(jsonb)", "infrx.dispatch_snapshot()", "infrx.expire_journal(jsonb)",
    "infrx.fail_dispatch(jsonb)", "infrx.fail_preparation(jsonb)", "infrx.gc_outbox(jsonb)",
    "infrx.heartbeat(jsonb)", "infrx.idempotency_lookup(jsonb)", "infrx.job_admission(uuid)",
    "infrx.journal_usage()", "infrx.load_work(jsonb)", "infrx.load_work_credit(jsonb)",
    "infrx.now()", "infrx.prepare(jsonb)", "infrx.put_result(jsonb)",
    "infrx.read_journal(jsonb)", "infrx.read_result(uuid,text)", "infrx.readiness_doc(uuid)",
    "infrx.recover(jsonb)", "infrx.release_dispatch(jsonb)", "infrx.reopen_dispatch(jsonb)",
    "infrx.terminalize(jsonb)", "infrx.upload_abort(jsonb)",
    "infrx.upload_acknowledge_put(jsonb)", "infrx.upload_complete(jsonb)",
    "infrx.upload_create(jsonb)", "infrx.upload_expire(jsonb)", "infrx.upload_resolve(jsonb)",
    "infrx.usd_price(text)"})


def check_door_revoke(owner, runtime) -> str:
    """DOOR-REVOKE (0023, R123): on the runtime LOGIN itself (`runtime`, no `set role`),
    each revoked door is refused by name, and the doors the W5 runtime calls still run
    (a refusal they answer is data, never a missing EXECUTE); `service_role` still runs the
    revoked ones; the cutover gate's `runtime_unmarked_doors` reads []."""
    def denied(conn, fn: str) -> bool:           # every door named here takes one jsonb
        name = fn.removesuffix("(jsonb)")
        try:
            with conn.transaction():
                conn.execute(f"select {name}(%s)", (Jsonb({}),))
        except psycopg.Error as refused:
            return refused.sqlstate == "42501" and \
                f"permission denied for function {name.split('.')[1]}" in str(refused)
        return False
    for fn in sorted(REVOKED_DOORS):
        assert denied(runtime, fn), f"infrx_runtime still executes {fn}"
    for fn in ("infrx.admit_ready(jsonb)", "infrx.claim_preparation_ready(jsonb)",
               "infrx.fail_preparation(jsonb)"):
        assert not denied(runtime, fn), f"infrx_runtime lost {fn}"
    for fn in sorted(REVOKED_DOORS):
        with owner.transaction():
            owner.execute("set local role service_role")
            assert not denied(owner, fn), f"service_role lost {fn}"
    gate, = owner.execute("select infrx.readiness_cutover_check()").fetchone()
    assert gate["runtime_unmarked_doors"] == [], gate
    return f"revoked {sorted(REVOKED_DOORS)}; {len(RUNTIME_FUNCTIONS)} runtime doors kept"


def check_reads_privileges(conn) -> str:
    """DUR-RLS for 0021: the consumer reads are the signed-in principal's only; a browser
    session never writes a key's audience, individual, provider or endpoint; the runtime role
    holds its grant list and no operator operation, money writer, browser table or DDL."""
    for fn, callers in (("public.consumer_jobs(text,integer,uuid,text,uuid,timestamp with "
                         "time zone,timestamp with time zone)",      # 0024's signature
                         {"authenticated", "service_role"}),
                        ("public.consumer_credit_ledger(text,integer)",   # 0024
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
    # DOOR-REVOKE: the exact surface, by name, catalog-wide (a grant anywhere fails it);
    # an extension's own functions (pgcrypto in `public` on the plain image) are not ours
    held = {f for f, in conn.execute(
        "select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = "
        "p.pronamespace where n.nspname in ('infrx', 'public') "
        "and has_function_privilege('infrx_runtime', p.oid, 'execute') and not exists "
        "(select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')")}
    assert held == RUNTIME_FUNCTIONS, ("infrx_runtime executes", sorted(held - RUNTIME_FUNCTIONS),
                                       "and not", sorted(RUNTIME_FUNCTIONS - held))
    for fn in REVOKED_DOORS:             # the operator's service login keeps the old doors
        allowed, = conn.execute("select has_function_privilege('service_role', %s, 'execute')",
                                (fn,)).fetchone()
        assert allowed, f"service_role lost {fn}"
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
                        ("infrx.audit_entries", "select"), ("infrx.job_results", "select"),
                        ("infrx.jobs", "update"), ("infrx.job_results", "insert"),
                        ("infrx.job_results", "update"), ("infrx.job_results", "delete")):
        has, = conn.execute("select has_table_privilege('infrx_runtime', %s, %s)",
                            (table, verb)).fetchone()
        assert not has, f"infrx_runtime may {verb} {table}"
    # the runtime's one job-row UPDATE is `updated_at` (a FOR UPDATE lock needs it): never
    # the persisted expiry, the outcome, the request record or the money
    updatable = {c for c, in conn.execute(
        "select a.attname from pg_attribute a where a.attrelid = 'infrx.jobs'::regclass "
        "and a.attnum > 0 and not a.attisdropped "
        "and has_column_privilege('infrx_runtime', a.attrelid, a.attnum, 'update')")}
    assert updatable == {"updated_at"}, f"infrx_runtime may update jobs.{sorted(updatable)}"
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
                                # 0024 (WR-W5F5-1): the CREDIT holds' state, never money
                                ("infrx.credit_wallet_holds", "state", "select"),
                                ("infrx.credit_wallet_holds", "amount", "select"),
                                ("infrx.jobs", "state", "update")):
        has, = conn.execute("select has_column_privilege('infrx_monitor', %s, %s, %s)",
                            (table, column, verb)).fetchone()
        assert has == (column in ("state", "claimed_at") and verb == "select"), \
            f"infrx_monitor {verb} {table}.{column}: {has}"
    return "consumer reads authenticated-only; key scope unwritable; runtime and monitor scoped"


__all__ = ["check_consumer_reads", "check_flag_freeze_race", "check_listing_card_authority", "check_reads_privileges",
           "check_reconcile_race", "check_result_expiry_persisted", "check_usd_resolution",
           "seed_hosted_usd"]
