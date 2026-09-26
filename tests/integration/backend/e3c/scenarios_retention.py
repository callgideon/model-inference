"""E3C s06 (RETENTION-DURABLE, RV-03) and s07 (RESULT-EXPIRY, RV-11).

s06: collectors run as FRESH processes (`world.collect_once`: one M6 pass over the box's real
object store with PostgreSQL's liveness) - two at once while a job is live, one while the
database is unreachable; past a result's persisted expiry the running worker's own
housekeeping (M6's entry: retention + journal prune) does the scrub. Oracles (F2C-L R112/R113):
live references protect their media; a pass that cannot ask the database deletes nothing;
past the persisted expiry the content-bearing rows are scrubbed while the metadata a bill
and an audit need stays; a replay never resurrects content.

s07: one persisted expiry (`jobs.result_expires_at`, written at settlement) governs every
read - status, result and replay, on every gateway process - across a policy change in
either direction and a restart. The read oracle is F2C-L's `v2.lifecycle.read_outcome` over
the store's own terminal outcome and clock when the tree has it; before that, the same rule
(R113: available while `now < result_expires_at`) read from the row."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

TTL_S = 600


def live_refs(trip, request_id: str) -> set[str]:
    """The object keys the job's durable attach names (its authorized media)."""
    return {ref for ref, in trip.db("select s.storage_ref from infrx.job_media m join "
                                    "infrx.staged_media s on s.org_id = m.org_id and "
                                    "s.handle = m.handle where m.job_id = %s", request_id)}


def kept(trip, refs: set[str], answers: list[dict]) -> None:
    present = world.objects(trip, "")
    lost = sorted(ref for ref in refs if ref not in present)
    assert not lost, f"a collector deleted a live job's media: {lost} (collectors: {answers})"


def test_s06_two_fresh_collectors_never_delete_a_live_jobs_media(workdir):
    """A job admitted and attached, not yet prepared (no worker): two collector processes at
    once, grace 0. Its media survives both, and the job then runs on it."""
    with world.composed(workdir, start=("gateway",)) as trip:
        alpha = trip.world.alpha
        accepted = trip.send(alpha, "async", world.video_url("s06-live"), "e3c-s06-live")
        assert accepted.status_code == 202, accepted.text
        request_id = accepted.json()["request_id"]
        refs = live_refs(trip, request_id)
        assert refs, "the admitted job has no durable attach to protect"
        # past the persisted grace: the media IS a candidate, only its live reference keeps it
        world.set_clock(trip.world.database, world.grace_passed())
        answers = world.collectors(trip, count=2, grace_s=0.0)
        world.set_clock(trip.world.database, 0.0)      # back inside the job's own deadlines
        kept(trip, refs, answers)
        world.collector_blocked(answers)
        trip.box.start("worker")
        assert world.terminal(trip, request_id, timeout=60.0) == "succeeded"
        world.settled_once(trip, request_id)


def test_s06_a_collector_that_cannot_reach_the_database_deletes_nothing(workdir):
    """Fail closed: with PostgreSQL unreachable (the namespace's container paused), a pass
    that cannot establish liveness deletes no object."""
    with world.composed(workdir, start=("gateway",)) as trip:
        accepted = trip.send(trip.world.alpha, "async", world.video_url("s06-dark"),
                             "e3c-s06-dark")
        assert accepted.status_code == 202, accepted.text
        before = world.objects(trip, "")
        world.set_clock(trip.world.database, world.grace_passed())   # eligible, if asked
        with stack.harness.Faults() as faults:
            faults.pause("postgres")
            answers = world.collectors(trip, count=1, grace_s=0.0)
        after = world.objects(trip, "")
        assert before <= after, f"deleted while the database was unreachable: " \
                                f"{sorted(before - after)} ({answers})"
        world.collector_blocked(answers)


def test_s06_past_expiry_content_is_scrubbed_and_metadata_kept(workdir):
    """A settled text job. The WORKER's housekeeping (M6's entry: retention + journal prune,
    at a 2 s cadence here) removes each content-bearing row when ITS persisted life ends:
    past the result expiry and the journal TTL the result body and the SSE deltas go while
    the request record stays (inside the job's content retention, 0020); past that
    retention the request record goes too. The job, its usage and its debit stay; the API
    answers 410 and a replay resurrects nothing."""
    with world.composed(workdir, RESULT_TTL_S=str(TTL_S), **world.HOUSEKEEPING) as trip:
        alpha = trip.world.alpha
        answer = trip.send(alpha, "sync", world.TEXT, "e3c-s06-scrub")
        assert answer.status_code == 200, answer.text
        text = answer.json()["choices"][0]["message"]["content"]
        request_id = answer.headers["inference-id"]
        handle = trip.handle_of(request_id)
        world.set_clock(trip.world.database, 4000.0)     # past the result TTL and the journal's
        left = world.housekept(trip, request_id, rows=world.RESULT_AND_JOURNAL)
        assert not left, f"content kept past its persisted expiry by the worker's " \
                         f"housekeeping: {left} (worker log: {trip.box.tail('worker')})"
        remaining = world.retention_left_s(trip, request_id)
        if remaining > 0:
            assert world.scrubbed_content(trip, request_id)[world.REQUEST_RECORD[0]], \
                f"the request record went {remaining:.0f} s before its persisted retention"
        assert trip.db("select state from infrx.jobs where request_id = %s",
                       request_id) == [("succeeded",)]
        assert trip.db("select count(*) from public.usage_events where id = %s",
                       request_id) == [(1,)]
        world.settled_once(trip, request_id)
        gone = trip.http.get(f"/v1/jobs/{handle}/result", headers=trip.headers(alpha))
        assert (gone.status_code, world.code(gone)) == (410, "result_expired"), gone.text
        replay = trip.send(alpha, "sync", world.TEXT, "e3c-s06-scrub")
        assert text not in replay.text, "a replay resurrected expired content"
        # last: past the idempotency TTL a same-key request is a new job, not a replay
        world.set_clock(trip.world.database, 4000.0 + max(remaining, 0.0) + 60.0)
        left = world.housekept(trip, request_id, rows=world.REQUEST_RECORD)
        assert not left, f"the request record kept past its persisted retention " \
                         f"({remaining:.0f} s after +4000 s): {left}"


# ------------------------------------------------------------------ s07


def expected_read(trip, tenant, handle: str, request_id: str) -> str:
    """What every read must answer now: F2C-L's `read_outcome` over the store's terminal
    outcome and clock where the tree has it; else R113 over the persisted row."""
    import asyncio
    now, = trip.one("select infrx.now()")
    try:
        from infrx.contracts.v2.lifecycle import read_outcome
    except ImportError:
        read_outcome = None
    if read_outcome is not None:
        from infrx.state.jobstore import PgJobStore, connector
        store = PgJobStore(connector(stack.harness.pg_dsn(trip.world.database)))
        _, outcome = asyncio.run(store.get_owned_credit(tenant.org_id, handle))
        return read_outcome(outcome, now).value
    expires, = trip.one("select result_expires_at from infrx.jobs where request_id = %s",
                        request_id)
    return "unavailable" if expires is None else ("available" if now < expires else "expired")


def reads_agree(trip, http, tenant, handle: str, request_id: str, text: str) -> str:
    """Status, result and a same-key replay through `http` all answer the persisted expiry."""
    expected = expected_read(trip, tenant, handle, request_id)
    persisted, = trip.one("select result_expires_at from infrx.jobs where request_id = %s",
                          request_id)
    status = http.get(f"/v1/jobs/{handle}", headers=trip.headers(tenant)).json()
    result = http.get(f"/v1/jobs/{handle}/result", headers=trip.headers(tenant))
    replay = http.post("/v1/chat/completions", headers=trip.headers(tenant, "e3c-s07"),
                       json={"model": stack.CREDIT_ALIAS, "messages": world.TEXT})
    seen = {"result_available": status.get("result_available"),
            "result_expires_at": status.get("result_expires_at"), "result": result.status_code,
            "replay_has_content": text in replay.text}
    if expected == "available":
        want = {"result_available": True,
                "result_expires_at": persisted.isoformat().replace("+00:00", "Z"),
                "result": 200, "replay_has_content": True}
    else:
        want = {"result_available": False, "result_expires_at": None, "result": 410,
                "replay_has_content": False}
    if seen["result_expires_at"] and want["result_expires_at"]:
        seen["result_expires_at"] = _instant(seen["result_expires_at"])
        want["result_expires_at"] = _instant(want["result_expires_at"])
    assert seen == want, f"a read disagrees with the persisted expiry ({expected}): " \
                         f"seen {seen}, persisted {persisted}"
    return expected


def _instant(value: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="milliseconds")


def settled_job(trip) -> tuple[str, str, str]:
    """(request id, handle, text) of one settled text job under key e3c-s07."""
    answer = trip.send(trip.world.alpha, "sync", world.TEXT, "e3c-s07")
    assert answer.status_code == 200, answer.text
    request_id = answer.headers["inference-id"]
    return request_id, trip.handle_of(request_id), answer.json()["choices"][0]["message"][
        "content"]


def test_s07_one_persisted_expiry_governs_every_read_across_a_policy_change(workdir):
    """Settled at RESULT_TTL_S=600. Then: the gateway restarted at 86400 (lengthened) and a
    second gateway at 30 (shortened) both answer the ORIGINAL expiry - available before it,
    410 at and after it (equality has passed), a replay never resurrecting the content."""
    import httpx
    with world.composed(workdir, RESULT_TTL_S=str(TTL_S)) as trip:
        alpha = trip.world.alpha
        request_id, handle, text = settled_job(trip)
        assert reads_agree(trip, trip.http, alpha, handle, request_id, text) == "available"
        trip.box.stop("gateway")
        trip.box.start("gateway", RESULT_TTL_S="86400")
        short = world.second_gateway(trip.box)
        short.start("gateway", RESULT_TTL_S="30")
        try:
            with httpx.Client(base_url=short.url, timeout=60.0) as b:
                world.set_clock(trip.world.database, 60.0)         # past 30 s, before 600 s
                for http in (trip.http, b):
                    assert reads_agree(trip, http, alpha, handle, request_id, text) == \
                        "available"
                persisted, = trip.one("select extract(epoch from result_expires_at - "
                                      "infrx.now()) from infrx.jobs where request_id = %s",
                                      request_id)
                world.set_clock(trip.world.database, 60.0 + float(persisted))  # AT the expiry
                for http in (trip.http, b):
                    assert reads_agree(trip, http, alpha, handle, request_id, text) == "expired"
        finally:
            short.stop("gateway")
        assert len(world.job_of(trip, alpha.org_id, "e3c-s07")) == 1
        world.settled_once(trip, request_id)


def test_nc_result_expiry__s07_detects_an_expiry_recomputed_from_the_current_policy(workdir):
    """Negative control: the gateway recomputes expiry from its current RESULT_TTL_S (bypass
    `expiry-recompute`, the base tree's rule); s07's read oracle must report the lengthened
    policy extending an already promised lifetime."""
    with world.composed(workdir, RESULT_TTL_S=str(TTL_S)) as trip:
        request_id, handle, text = settled_job(trip)
        trip.box.stop("gateway")
        trip.box.start("gateway", RESULT_TTL_S="86400", INFRX_E3C_BYPASS="expiry-recompute")
        with pytest.raises(AssertionError, match="disagrees with the persisted expiry"):
            reads_agree(trip, trip.http, trip.world.alpha, handle, request_id, text)
