"""D10 follow-up (0022): the fenced `fail_preparation` (W5 wiring request 3) and the fair,
bounded flag writer `set_feature_flag` (G8 V-G8TL-2) as SQL-level checks.

Same contract as `checks_ready.py`: the "admission" scenario; `check_fail_preparation` and
`check_followup_privileges` roll back, the two flag races commit on their own connections
and restore the flag.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

from psycopg.types.json import Jsonb

from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS

from . import checks_admission as ca
from . import checks_content as ck
from . import checks_credit as cc
from . import checks_ready as cr
from . import checks_settle as cs
from .checks_dispatch import advance, claim, kinds, outcome, prepare
from .checks_leases import LIMITS, d3, dump, running, waiting_on_a_lock

FLAG = "legacy_usd_admission"
#: G8's shape: eight lockers, each holding the flag FOR SHARE 200 ms, overlapping.
LOCKERS, HOLD_S, BOUND_S, MARGIN_S = 8, 0.2, 2.0, 1.0


def fail(conn, lease: dict, cause) -> tuple[str | None, object]:
    return outcome(conn, "fail_preparation", {"lease": lease, "cause": cause, "limits": LIMITS})


def _money(conn, request_id: str) -> tuple:
    """(job state, cause, settlement, hold state, active reservations, live attempts,
    usage/trace projections, inference dispatches, wallet reserved + ledger in the job's
    own unit) - everything a preparation end may move."""
    return conn.execute("""select j.state, j.outcome_cause, j.settlement_state,
        coalesce((select state from infrx.credit_holds where request_id = j.request_id),
                 (select state from infrx.credit_wallet_holds where request_id = j.request_id)),
        (select count(*) from infrx.capacity_reservations where request_id = j.request_id
            and active),
        (select count(*) from infrx.attempts where job_id = j.request_id
            and released_at is null),
        (select count(*) from infrx.outbox where aggregate_id = j.request_id
            and kind in ('usage_projection', 'trace_projection')),
        (select count(*) from infrx.outbox where aggregate_id = j.request_id
            and kind = 'inference_dispatch'),
        case j.accounting_regime
          when 'credit' then (select reserved_total::text || '/' || ledger_total::text
                                from infrx.credit_wallets where wallet_id = j.wallet_id)
          else (select reserved_total::text || '/' || ledger_total::text
                  from infrx.wallets where org_id = j.org_id) end
        from infrx.jobs j where j.request_id = %s""", (request_id,)).fetchone()


def _preparing(conn, world, regime: str = "legacy_usd"):
    if regime == "credit":
        request = ca.credit_request(world, ca.C1_KEY, cc.personal_org(conn, cc.CONSUMER_1))
        ca.admit(conn, request, b.idem(request, request.request_id), regime="credit")
    else:
        request = b.request(world)
        ca.admit(conn, request, b.idem(request, request.request_id))
    code, answer = claim(conn, request.request_id)
    assert code is None, code
    return request, answer["lease"]


def check_fail_preparation(conn) -> str:
    """W5 request 3: only a permanent cause, only the live preparation lease (kind,
    generation, owner, expiry, R29 phase deadline) ends the job - `failed`, no usage,
    released_free, hold/reservations/attempt released, one usage + one trace projection, no
    inference dispatch - in both regimes; the wallet returns exactly the hold (nothing
    debited). The same lease's identical call replays the committed outcome with nothing
    moved; any other call after the end is `already_terminal`; a refused call moves nothing."""
    world = ca.World(conn)

    def body():
        report = []
        for regime, cause in (("legacy_usd", "invalid_media"), ("credit", "preparation_failed")):
            request, lease = _preparing(conn, world, regime)
            rid = request.request_id
            live = _money(conn, rid)
            assert live[:4] == ("preparing", None, None, "held") and live[4] > 0 \
                and live[5:8] == (1, 0, 0), (regime, live)
            # refused: a cause that is not a preparation's end, a forged or wrong-kind lease
            for bad in ("engine_error", "completed", "client_cancelled", None):
                assert fail(conn, lease, bad)[0] == "invalid_request", (regime, bad)
            for label, forged in (("a foreign worker", dict(lease, worker_id="prep-b")),
                                  ("a stale generation", dict(lease, generation=2)),
                                  ("an inference token", dict(
                                      lease, kind="inference",
                                      first_token_deadline_at=lease["expires_at"]))):
                assert fail(conn, forged, cause)[0] == "stale_lease", (regime, label)
            assert _money(conn, rid) == live, f"{regime}: a refused call moved something"
            # the end
            code, doc = fail(conn, lease, cause)
            assert code is None, (regime, code)
            ended = doc["outcome"]
            assert (ended["state"], ended["cause"], ended["settlement_state"], ended["usage"]) \
                == ("failed", cause, "released_free", None), (regime, ended)
            after = _money(conn, rid)
            before_reserved, ledger = live[8].split("/")
            reserved, ledger_after = after[8].split("/")
            assert after[:8] == ("failed", cause, "released_free", "released", 0, 0, 2, 0), \
                (regime, after)
            assert ledger_after == ledger, f"{regime}: the ledger moved ({ledger} -> " \
                                           f"{ledger_after})"
            hold = conn.execute("select amount from infrx.credit_holds where request_id = %s "
                                "union all select amount from infrx.credit_wallet_holds "
                                "where request_id = %s", (rid, rid)).fetchone()[0]
            assert float(before_reserved) - float(reserved) == float(hold), \
                (regime, before_reserved, reserved, hold)
            # replay: the identical call answers the committed outcome, nothing moves
            code, again = fail(conn, lease, cause)
            assert code is None and again["outcome"] == ended, (regime, code, again)
            assert _money(conn, rid) == after, f"{regime}: the replay moved something"
            # anything else after the end
            other = "preparation_failed" if cause == "invalid_media" else "invalid_media"
            assert fail(conn, lease, other)[0] == "already_terminal", regime
            code, next_lease = claim(conn, rid, "prep-b")
            assert code == "already_terminal", (regime, code)
            assert fail(conn, dict(lease, worker_id="prep-b"), cause)[0] == \
                "already_terminal", f"{regime}: another worker replayed this lease's end"
            assert fail(conn, dict(lease, generation=lease["generation"] + 1), cause)[0] == \
                "already_terminal", f"{regime}: another generation replayed this lease's end"
            assert prepare(conn, lease)[0] == "already_terminal", regime
            assert _money(conn, rid) == after, regime
            report.append(f"{regime}:{cause}")
        # a lapsed lease is stale and moves nothing; the job stays preparable
        request, lease = _preparing(conn, world)
        advance(conn, DEFAULTS.preparation_lease_ttl_s + 1)
        live = _money(conn, request.request_id)
        assert fail(conn, lease, "invalid_media")[0] == "stale_lease", "a lapsed lease ended it"
        assert _money(conn, request.request_id) == live
        code, answer = claim(conn, request.request_id, "prep-next")
        assert code is None and answer["lease"]["generation"] == 2, code
        # the prepared job's spent preparation lease is stale (no live preparation attempt)
        request, lease = _preparing(conn, world)
        assert prepare(conn, lease)[0] is None
        assert fail(conn, lease, "invalid_media")[0] == "stale_lease", "a queued job ended"
        # an inference lease never ends a preparation
        request, inference = running(conn, world, "w-fp")
        assert fail(conn, dump(inference), "invalid_media")[0] == "stale_lease", \
            "an inference lease ended the job through the preparation door"
        # past the phase deadline: R29's terminalization wins (committed, answered as data)
        request, lease = _preparing(conn, world)
        advance(conn, DEFAULTS.preparation_timeout_s)
        assert fail(conn, lease, "invalid_media")[0] == "already_terminal"
        assert _money(conn, request.request_id)[1] == "preparation_failed"
        # a client's cancel first: its cause stands, settled once
        request, lease = _preparing(conn, world)
        assert d3(conn, "cancel", org_id=str(request.org_id),
                  job_handle=conn.execute("select job_handle from infrx.jobs where "
                                          "request_id = %s", (request.request_id,)
                                          ).fetchone()[0])[0] is None
        assert fail(conn, lease, "invalid_media")[0] == "already_terminal"
        state = _money(conn, request.request_id)
        assert state[1] == "client_cancelled" and state[6] == 2, state
        report.append("lapsed/queued/inference stale, deadline and cancel win")
        return "; ".join(report)
    return ca._in_rollback(conn, body)


def check_followup_privileges(conn) -> str:
    """0022's grants: `fail_preparation` is the worker's (service_role, infrx_runtime);
    `set_feature_flag` is the operator's (service_role alone); 0021's trigger guard
    `jobs_result_expiry_guard` nobody's. No browser principal executes any; all are SECURITY
    DEFINER with a fixed search_path. The writer answers whether
    it changed the row, attributed."""
    for fn, callers in (("infrx.fail_preparation(jsonb)", {"service_role", "infrx_runtime"}),
                        ("infrx.set_feature_flag(text,boolean,text,text)", {"service_role"}),
                        # L3-REBASE F2: a trigger guard, executable by nobody (0019/0020's)
                        ("infrx.jobs_result_expiry_guard()", set())):
        for role in ("public", "anon", "authenticated", "service_role", "infrx_runtime"):
            if role == "public":
                acl, secdef, config = conn.execute(
                    "select coalesce(proacl::text, ''), prosecdef, proconfig from pg_proc "
                    "where oid = %s::regprocedure", (fn,)).fetchone()
                assert "{=X" not in acl and ",=X" not in acl, (fn, acl)
                assert secdef and any(c.startswith("search_path=") for c in config), fn
                continue
            allowed, = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                    (role, fn)).fetchone()
            assert allowed == (role in callers), (fn, role, allowed)
    for session in ("anon", "member", "owner"):
        why = cc.attempt(conn, "select infrx.set_feature_flag('signup_grant', true, 'x', 'y')",
                         session=session)
        assert why is not None and why.startswith("42501"), (session, why)
    written = []

    def body():
        conn.execute("set local role service_role")
        for enabled in (False, False, True):
            written.append(conn.execute(
                "select infrx.set_feature_flag(%s, %s, %s, %s)",
                (FLAG, enabled, "op:" + "x" * 300, "r" * 600)).fetchone()[0])
        row = conn.execute("select enabled, length(updated_by), length(reason) from "
                           "infrx.feature_flags where name = %s", (FLAG,)).fetchone()
        assert row == (True, 200, 500), row
        assert conn.execute("select infrx.set_feature_flag('no_such_flag', true, 'x', 'y')"
                            ).fetchone()[0] is False
        return None
    ca._in_rollback(conn, body)
    assert written == [True, False, True], written
    return "fail_preparation: worker roles; set_feature_flag: service_role; changed-or-not"


def _write(connect, database, enabled: bool, box: dict) -> None:
    """The operator's write as G8 issues it: service_role, SET LOCAL bounds, the function."""
    writer = cs.service(connect, database)
    started = time.monotonic()
    try:
        writer.execute("begin")
        writer.execute(f"set local lock_timeout = {int(BOUND_S * 1000)}")
        writer.execute(f"set local statement_timeout = {int((BOUND_S + MARGIN_S) * 1000)}")
        box["changed"] = writer.execute("select infrx.set_feature_flag(%s, %s, 'd10', 'probe')",
                                        (FLAG, enabled)).fetchone()[0]
        writer.execute("commit")
    except Exception as exc:                        # 55P03 / 57014: the bound, refused
        box["refused"] = getattr(exc, "sqlstate", repr(exc))
        writer.execute("rollback")
    finally:
        box["elapsed"] = time.monotonic() - started
        writer.close()


def check_flag_writer_queues_new_readers(connect, database: str) -> str:
    """V-G8TL-2, deterministic: an admission holds the flag FOR SHARE; the writer waits; a
    NEW admission's `require_feature` then queues BEHIND the writer (a row-level UPDATE let
    it join the share lock ahead of the writer). When the first commits, the write lands
    (True) and the queued admission sees the freeze (`maintenance`)."""
    owner = connect(database)
    holder, reader = connect(database), connect(database)
    box: dict = {}
    out: dict = {}
    try:
        holder.execute("begin")
        holder.execute("select infrx.require_feature(%s)", (FLAG,))
        writer = threading.Thread(target=_write, args=(connect, database, False, box))
        writer.start()
        time.sleep(0.2)
        pid = owner.execute("select pid from pg_stat_activity where query like "
                            "'select infrx.set_feature_flag%%' and state = 'active'").fetchone()
        assert pid is not None, f"the writer is not running: {box}"
        waiting_on_a_lock(owner, pid[0])

        def late():
            try:
                reader.execute("select infrx.require_feature(%s)", (FLAG,))
                out["late"] = "admitted"
            except Exception as exc:
                out["late"] = getattr(exc, "sqlstate", repr(exc))
        thread = threading.Thread(target=late)
        thread.start()
        try:
            waiting_on_a_lock(owner, reader.info.backend_pid, within_s=3.0)
        except AssertionError:
            raise AssertionError(f"a new FOR SHARE reader passed the waiting writer: {out}")
        finally:
            holder.execute("commit")
            writer.join(10)
            thread.join(10)
        assert box.get("changed") is True, f"the write did not land: {box}"
        assert out["late"] == "55000", f"the queued admission after the freeze: {out}"
    finally:
        for c in (holder, reader):                  # an open share lock would block the reset
            c.close()
        cc.set_flag(owner, FLAG, True)
        owner.close()
    return f"a new reader queued behind the writer; write landed in {box['elapsed']:.2f}s"


def check_flag_writer_lands_under_overlapping_lockers(connect, database: str) -> str:
    """V-G8TL-2, G8's overlapping-lockers probe with the assertion tightened: eight
    connections loop `require_feature; pg_sleep(0.2); commit`, overlapping, so the row is
    never free of share lockers; the bounded write must CHANGE the flag within the bound.
    A row-level writer (0006's UPDATE) or a SHARE ROW EXCLUSIVE table lock only times out."""
    owner = connect(database)
    stop = threading.Event()

    def locker():
        conn = connect(database, autocommit=False)
        try:
            while not stop.is_set():
                try:
                    conn.execute("select infrx.require_feature(%s)", (FLAG,))
                    conn.execute("select pg_sleep(%s)", (HOLD_S,))
                    conn.commit()
                except Exception:                             # the flag is off: refused
                    conn.rollback()
                    time.sleep(0.01)
        finally:
            conn.close()
    lockers = [threading.Thread(target=locker, daemon=True) for _ in range(LOCKERS)]
    box: dict = {}
    try:
        for t in lockers:
            t.start()
        time.sleep(0.3)                                       # the lockers overlap by now
        writer = threading.Thread(target=_write, args=(connect, database, False, box))
        writer.start()
        writer.join(30)
        assert not writer.is_alive(), "the flag write did not return within 30 s"
    finally:
        stop.set()
        for t in lockers:
            t.join(10)
        cc.set_flag(owner, FLAG, True)
        owner.close()
    assert box.get("changed") is True, f"the bounded write did not land: {box}"
    assert box["elapsed"] < BOUND_S + MARGIN_S + 1.5, box
    return f"{LOCKERS} overlapping lockers: the write landed in {box['elapsed']:.2f}s"


def check_written_reregistration_refreshes(conn) -> str:
    """M6 WR-7 (0022): a `written` registration of the SAME bytes at a live key restarts its
    grace - `eligible_at = greatest(eligible_at, now + grace)` - so a clip fetched again
    after its first row became eligible is not collected before its admission; a claim
    granted before the refresh can no longer tombstone it (`not_eligible`). A `discovered`
    registration never refreshes; a shorter grace never shortens; no writer moves a live
    eligibility earlier or a retiring row's at all (the guard)."""
    def body():
        org = cr.c1_org(conn)
        clip = cr.source_ref(org, b"refetched-clip")
        first = cr.register(conn, clip)
        ck.at(conn, first["eligible_at"])
        advance(conn, cr.GRACE_S)                        # eligible for a while now
        row = ck.row_of(conn, clip.storage_ref)
        claim = cr.call(conn, "content_claim", ck._claim_args(row, "sweeper-wr7"))
        again = cr.register(conn, clip)                  # request 2 fetches the same clip
        now = conn.execute("select infrx.now()").fetchone()[0]
        assert again["content_id"] == first["content_id"] and \
            ck.row_of(conn, clip.storage_ref)["eligible_at"] == \
            now + timedelta(seconds=cr.GRACE_S), (first, again)
        got = cr.refusal(conn, "content_tombstone", {"claim": claim})
        assert got == ("not_claimable", "not_eligible"), \
            f"a claim from before the refetch tombstoned the refetched bytes: {got}"
        refreshed = ck.row_of(conn, clip.storage_ref)["eligible_at"]
        advance(conn, 1)
        found = cr.call(conn, "content_register", {"identity": {
            **again["identity"], "origin": "discovered"}, "grace_s": cr.GRACE_S})
        assert ck.row_of(conn, clip.storage_ref)["eligible_at"] == refreshed, \
            f"a discovered registration refreshed the grace: {found}"
        why = cc.attempt(conn, "select infrx.content_register(%s)", (Jsonb(
            {"identity": again["identity"], "grace_s": 0}),))
        assert why is None, f"a shorter-grace written registration was refused: {why}"
        assert ck.row_of(conn, clip.storage_ref)["eligible_at"] == refreshed, \
            "a shorter grace shortened the eligibility"
        for sql in ("update infrx.content_objects set eligible_at = eligible_at - "
                    "interval '1 second' where object_key = %s",):
            why = cc.attempt(conn, sql, (clip.storage_ref,))
            assert why is not None and why.startswith("23514"), f"moved earlier: {why}"
        conn.execute("update infrx.content_objects set state = 'tombstoned', tombstoned_at = "
                     "infrx.now() where object_key = %s", (clip.storage_ref,))
        why = cc.attempt(conn, "update infrx.content_objects set eligible_at = eligible_at + "
                         "interval '1 day' where object_key = %s", (clip.storage_ref,))
        assert why is not None and why.startswith("23514"), f"a retiring row moved: {why}"
        return "written refetch restarts the grace and defeats an earlier claim; " \
               "discovered/shorter never; guard: later-only, live-only"
    return ca._in_rollback(conn, body)


__all__ = ["check_fail_preparation", "check_written_reregistration_refreshes", "check_flag_writer_lands_under_overlapping_lockers",
           "check_flag_writer_queues_new_readers", "check_followup_privileges"]
