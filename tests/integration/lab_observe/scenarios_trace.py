"""E5L trace scenarios: capture -> ship -> search, feedback, Lab review, content expiry and
deletion, the projection dropped, a worker restarted - on the e5l stack (real PostgreSQL,
PostgREST, Valkey, ClickHouse, MinIO; E3C's gateway + worker + controlled engine). Run only
through `runner.py` (never collected by `pytest tests/integration`: no `test_` prefix).
"""
from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import observe_world as ow                              # noqa: E402

world, stack, harness, run = ow.world, ow.stack, ow.harness, ow.run
CONTENT = b'{"q":"Describe the van."}'


def pins_of(trip, request_id: str) -> tuple:
    pins = trip.one("select infrx.job_admission(%s)", request_id)[0]["pins"]
    return (pins["serving_version_id"], pins["rate_card_version"])


# --- o01 capture -> ship -> search ------------------------------------------------------------
def test_o01_capture_is_off_by_default_and_a_served_request_leaves_no_trace(workdir):
    with ow.observe_trip(workdir) as trip:
        env = trip.box.env
        assert not {"TRACE_SPOOL_DIR", "CLICKHOUSE_URL", "TRACE_PUMPS"} & set(env), \
            "premise: the box runs with capture and the pumps at their defaults"
        request_id = ow.served(trip, trip.world.alpha, "o01-off")
        assert trip.traces.rows("trace_envelopes", request_id) == 0
        stack_db = harness.clickhouse_client().query(
            "select count() from trace_envelopes where request_id = {r:UUID}",
            parameters={"r": request_id}).result_rows[0][0]
        assert stack_db == 0, "a served request left a trace with capture off"


def test_o01_a_captured_request_ships_once_with_its_pins_and_only_its_org_finds_it(workdir):
    with ow.observe_trip(workdir) as trip:
        alpha, beta = trip.world.alpha, trip.world.beta
        ids = [ow.served(trip, alpha, f"o01-{n}") for n in range(2)]
        ow.captured(trip, alpha, ids, CONTENT)
        report = ow.shipped(trip)
        assert (report.shipped, report.rows, report.held) == (1, 2, {}), report
        retention = trip.traces.retention
        for request_id in ids:
            [row] = run(retention.find_traces(alpha.org_id, request_id))
            assert (row.serving_version_id, row.rate_card_version) == pins_of(trip, request_id)
            assert row.content_key.startswith(f"trace/{alpha.org_id}/")
            assert run(retention.read_content(alpha.org_id, request_id)) == CONTENT
            assert run(retention.find_traces(beta.org_id, request_id)) == []
            assert run(retention.read_content(beta.org_id, request_id)) is None
        assert ow.shipped(trip).rows == 0, "a second pass re-shipped acknowledged segments"
        assert all(trip.traces.rows("trace_envelopes", r) == 1 for r in ids)


def capture_on(trip, workdir) -> Path:
    """TRACE_PUMPS on in the box (worker then gateway on one TRACE_SPOOL_DIR, WR-LC-LOCAL):
    both orgs consent `full`; only alpha's key opted in. The spool directory."""
    alpha, beta = trip.world.alpha, trip.world.beta
    for tenant in (alpha, beta):
        ow.sql(trip, "insert into infrx.consent_history (org_id, consent_version, "
                     "trace_mode, content_retention_days, evaluation_consent, "
                     "actor_principal, effective_at) select %s::uuid, coalesce(max("
                     "consent_version), 0) + 1, 'full', 30, false, 'e5l', infrx.now() - "
                     "interval '1 minute' from infrx.consent_history where org_id = %s::uuid",
               tenant.org_id, tenant.org_id)
    ow.sql(trip, "update public.api_keys set trace_mode = 'full' where id = %s", alpha.key_id)
    spool = workdir / "trace-spool"
    on = {"TRACE_PUMPS": "1", "TRACE_SPOOL_DIR": str(spool),
          "CLICKHOUSE_URL": ow.limits(trip.traces).clickhouse_url,
          "S3_TRACE_BUCKET": harness.S3_BUCKET}
    trip.box.start("worker", **on)
    trip.box.start("gateway", **on)
    return spool


def echoing(tenant) -> list:
    """A prompt that echoes the caller's own bearer token (the credential a record never
    holds; the spool itself is drained by the ship pass, so the shipped record is judged)."""
    return [{"role": "user", "content": f"Describe the van. {tenant.secret}"}]


def holds_no_token(trip, tenant, request_id: str, half: str) -> None:
    """The shipped record of `request_id` holds the redaction and never the caller's token.
    The failure names the half and the byte class, never the token (no assertion rewrite
    over the bytes)."""
    from infrx.gateway.capture import REDACTED
    content = run(trip.traces.retention.read_content(tenant.org_id, request_id))
    request_part, _, answer_part = content.partition(b"\n")
    token = tenant.secret.encode()
    where = [name for name, part in (("request line", request_part),
                                     ("answer", answer_part)) if token in part]
    assert not where, f"{half}: the caller's bearer token in the shipped record's {where}"
    assert REDACTED in content, f"{half}: the echoed token was not replaced by the redaction"


def test_o01_capture_turned_on_through_the_composition_switch(workdir):
    """WR-C6-CAPTURE (R250): `TRACE_PUMPS` on in the box (gateway and worker on one
    TRACE_SPOOL_DIR). alpha's key opted in under alpha's consent: its sync answer (the gateway's
    hook) and its async job's output (the worker's job spool) are shipped by the gateway's
    lifespan with their pins and found by alpha only; the async record holds the controlled
    engine's answer (the job's output, WR-LO4-RV2). beta's org consents but its key never
    opted in: its async and sync requests leave 0 envelopes (WR-LO4-RV3). alpha's sync prompt
    echoes its own token: the shipped record holds the redaction, never the token (WR-LO4-RV1;
    the async half is the next case)."""
    with ow.observe_trip(workdir, start=(), trace_prefix="infrx/") as trip:
        alpha, beta = trip.world.alpha, trip.world.beta
        spool = capture_on(trip, workdir)
        sync = trip.send(alpha, "sync", echoing(alpha), None)
        assert sync.status_code == 200, sync.text[:200]
        echoed = sync.headers["inference-id"]
        job = ow.served(trip, alpha, "o01-on")
        captured = {echoed, job}
        beta_sync = trip.send(beta, "sync", world.TEXT, None)
        assert beta_sync.status_code == 200, beta_sync.text[:200]
        quiet = {ow.served(trip, beta, "o01-beta"), beta_sync.headers["inference-id"]}
        retention = trip.traces.retention
        world.wait_for(lambda: all(run(retention.find_traces(alpha.org_id, r))
                                   for r in captured), 60.0, "the gateway's ship pass")
        for request_id in captured:
            [row] = run(retention.find_traces(alpha.org_id, request_id))
            assert row.mode == "full" and row.content_stored, row
            assert (row.serving_version_id, row.rate_card_version) == pins_of(trip, request_id)
            assert b"Describe the van." in run(retention.read_content(alpha.org_id, request_id))
            assert run(retention.find_traces(beta.org_id, request_id)) == []
        answer = trip.engine.control()["text"].encode()
        assert answer in run(retention.read_content(alpha.org_id, job)), \
            "async: the job's output is not in its shipped record"
        holds_no_token(trip, alpha, echoed, "sync")
        # one more ship pass (every 10 s): the box exposes no pass counter to wait on
        time.sleep(12.0)
        for request_id in quiet:
            assert run(retention.find_traces(beta.org_id, request_id)) == []
            assert trip.traces.rows("trace_envelopes", request_id) == 0, \
                "a key that never opted in left a trace"
        assert all(trip.traces.rows("trace_envelopes", r) == 1 for r in captured)
        assert not any((spool / "jobs").iterdir()), "a shipped job spool was left"


def test_o01_an_async_jobs_shipped_record_holds_no_caller_token(workdir):
    """The async half of WR-LO4-RV1: alpha's job prompt echoes its own token; the worker's
    job spool (JobCapture) is shipped by the gateway, and the record holds the redaction,
    never the token. Known FAIL on this base: R8 (lab-capture-2) - `JobCapture.spool` writes
    `request_line(request)` without the caller's token (mutants.KNOWN_FAIL)."""
    with ow.observe_trip(workdir, start=(), trace_prefix="infrx/") as trip:
        alpha = trip.world.alpha
        capture_on(trip, workdir)
        request_id = ow.served(trip, alpha, "o01-echo", messages=echoing(alpha))
        retention = trip.traces.retention
        world.wait_for(lambda: run(retention.find_traces(alpha.org_id, request_id)), 60.0,
                       "the gateway's ship pass")
        holds_no_token(trip, alpha, request_id, "async")


# --- o02 feedback -------------------------------------------------------------------------
def test_o02_feedback_is_acknowledged_after_commit_owned_by_its_key_and_projected_once(workdir):
    from infrx.state.jobstore import connector
    from infrx.traces.feedback import FeedbackProjector
    from infrx.traces.feedback.pg import PgFeedbackOutbox
    with ow.observe_trip(workdir, start=("worker",)) as trip:
        ow.sql(trip, "insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                     "('feedback', true, 'e5l', 'E5L o02') on conflict (name) do update set "
                     "enabled = true")
        trip.box.start("gateway", FEEDBACK_API="1")
        alpha, beta = trip.world.alpha, trip.world.beta
        request_id = ow.served(trip, alpha, "o02")
        signal = {"request_id": request_id, "name": "rating", "value": 4, "comment": "close"}

        def post(tenant, body, key):
            return trip.http.post("/v1/feedback", json=body,
                                  headers=trip.headers(tenant, key))
        first = post(alpha, signal, "e5l-o02-1")                 # before any trace projection
        assert first.status_code == 201, first.text
        replay = post(alpha, signal, "e5l-o02-1")
        assert replay.status_code == 201 and replay.json()["feedback_id"] == \
            first.json()["feedback_id"]
        foreign = post(beta, signal, "e5l-o02-2")
        assert foreign.status_code == 404, foreign.text
        spoofed = post(alpha, {**signal, "author_role": "operator"}, "e5l-o02-3")
        assert spoofed.status_code == 400, spoofed.text
        assert trip.one("select count(*) from infrx.feedback where request_id = %s",
                        request_id)[0] == 1
        projector = FeedbackProjector(PgFeedbackOutbox(connector(trip.traces.dsn)),
                                      trip.traces.retention.feedback,
                                      retention=trip.traces.retention)
        assert run(projector.pump()) == {"read": 1, "projected": 1, "acknowledged": 1,
                                         "orphaned": 0}
        [row] = run(trip.traces.retention.find_feedback(alpha.org_id, request_id))
        assert row.feedback_id == first.json()["feedback_id"]
        assert str(row.author_role) in ("customer", "AuthorRole.customer")
        assert run(projector.pump())["read"] == 0


# --- o03 Lab review -------------------------------------------------------------------------
def test_o03_a_provider_reads_a_grantors_trace_only_under_a_current_sharing_grant(workdir):
    from infrx.contracts import errors
    from infrx.contracts.v2.records import DataCategory, DataPurpose
    with ow.observe_trip(workdir) as trip:
        alpha, beta = trip.world.alpha, trip.world.beta
        request_id = ow.served(trip, alpha, "o03")
        ow.captured(trip, alpha, [request_id], CONTENT)
        assert ow.shipped(trip).rows == 1
        access = ow.access(trip)

        def review(user: str, grantor) -> bytes | None | str:
            try:
                run(access.authorize_content(
                    user_id=user, provider_org_id=ow.PROVIDER, grantor_org_id=grantor.org_id,
                    model_id=ow.MODEL, category=DataCategory.request_content,
                    purpose=DataPurpose.provider_sharing))
            except (errors.Forbidden, errors.NotFound) as denied:
                return type(denied).__name__
            return run(trip.traces.retention.read_content(grantor.org_id, request_id))
        assert review(ow.DEV, alpha) == CONTENT
        assert review(alpha.user_id, alpha) in ("Forbidden", "NotFound"), "a non-member read"
        assert review(ow.DEV, beta) in ("Forbidden", "NotFound"), "no grant, no content"
        ow.revoke(trip, alpha)
        assert review(ow.DEV, alpha) in ("Forbidden", "NotFound"), "revoked, still read"


def test_o03_the_lab_traces_route_through_the_real_gateway(workdir):
    """LAB-API's `/lab/v1/traces` on the box's gateway (LAB_TRACES on, the forwarded session
    verified by the auth server's path, L2's access, T2I's projection, T3's retention): DEV
    lists and reads alpha's request with its content under alpha's current grant; a consumer's
    API key is no session; alpha's own user (no membership of A) is refused; revoked, the row
    is metadata only (no organization, no content); deleted by its owner, it is gone.
    Oracles: a route reading the raw projection shows the deleted request; one that skips the
    grant shows content after the revocation."""
    with ow.observe_trip(workdir, start=("worker",), trace_prefix="infrx/") as trip, \
            ow.supabase_door(trip.box.env["SUPABASE_URL"]) as door:
        trip.box.start("gateway", **ow.lab_traces_env(trip, door))
        alpha = trip.world.alpha
        request_id = ow.served(trip, alpha, "o03-route")
        ow.captured(trip, alpha, [request_id], CONTENT)
        assert ow.shipped(trip).rows == 1

        def get(path: str, token: str):
            return trip.http.get(path, params={"provider_org_id": ow.PROVIDER},
                                 headers={"authorization": f"Bearer {token}"})
        dev, one = ow.session(ow.DEV), f"/lab/v1/traces/{request_id}"
        listed = get("/lab/v1/traces", dev)
        assert listed.status_code == 200, listed.text
        [item] = [i for i in listed.json()["data"] if i["request_id"] == request_id]
        assert (item["access"], item["grantor_org_id"]) == ("content", alpha.org_id), item
        read = get(one, dev)
        assert read.status_code == 200 and read.json()["content"] == CONTENT.decode(), read.text
        assert get(one, alpha.secret).status_code == 401, "an API key read the Lab route"
        assert get(one, ow.session(alpha.user_id)).status_code in (403, 404), "a non-member read"
        ow.revoke(trip, alpha)
        after = get(one, dev)
        assert after.status_code == 200, after.text
        assert after.json()["access"] == "metadata" and not {"content", "grantor_org_id"} & set(
            after.json()), after.json()
        run(trip.traces.retention.delete(alpha.org_id, request_id, "owner"))
        assert get(one, dev).status_code == 404, "an owner-deleted request is still read"
        assert request_id not in {i["request_id"] for i in get("/lab/v1/traces", dev).json()["data"]}
        assert door.seen and all(door.seen[:2]), "the session was never verified"


# --- o10 the Lab review panel --------------------------------------------------------------
def test_o10_the_lab_review_panel_renders_the_routes_answer(workdir):
    """CONSOLE-FLOWS (LAB-E2E): `apps/lab/tests/e2e/observe` - the Lab's requests list and
    review page built and served, signed in through the Lab's own form, over the provider trace
    route on the real L2 and registry (l4): each request's content state, the review panels'
    own answers (0038's feedback door, 0043's judge door, C2's unwired content port), the
    viewer / other-provider / consumer / signed-out variants and a revocation. The ClickHouse
    projection is o03's; here T2I/T3 are the in-memory stand-ins (the l4 key has none)."""
    import importlib.util
    import os
    repo = Path(__file__).resolve().parents[3]
    lab = Path(os.environ.get("INFRX_LAB_DIR") or repo / "apps" / "lab")
    spec = importlib.util.spec_from_file_location("lab_observe.lab_e2e_gate",
                                                  lab / "tests" / "e2e" / "gate.py")
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    got = e2e.run("observe", workdir)
    assert e2e.missing(got) == [], got["record"]


# --- o06 expiry and deletion (the read side; the judge side is scenarios_judge) --------------
def test_o06_expired_or_deleted_content_is_gone_for_every_read_before_and_after_the_sweep(
        workdir):
    from infrx.traces.retention import Retention
    with ow.observe_trip(workdir) as trip:
        alpha = trip.world.alpha
        expiring, deleted = (ow.served(trip, alpha, f"o06-{n}") for n in range(2))
        ow.captured(trip, alpha, [expiring, deleted], CONTENT)
        assert ow.shipped(trip).rows == 2
        live = trip.traces.retention
        later = Retention(live.store, live.traces, live.feedback, live.objects,
                          content_days=live.content_days, metadata_months=live.metadata_months,
                          clock=lambda: ow.Wall.now() + timedelta(days=live.content_days + 1))
        assert run(later.expire()) >= 1
        run(live.delete(alpha.org_id, deleted, "owner"))
        assert run(later.read_content(alpha.org_id, expiring)) is None
        assert run(live.find_traces(alpha.org_id, deleted)) == []
        assert run(live.read_content(alpha.org_id, deleted)) is None
        swept = run(later.sweep())
        assert swept["cleaned"] >= 2 and swept["failed"] == 0, swept
        [row] = run(live.traces.find(alpha.org_id, expiring))       # metadata stays
        assert run(live.objects.get(row.content_key)) is None, "expired content not deleted"
        assert trip.traces.rows("trace_envelopes", deleted) == 0, "deleted rows not purged"


# --- o07 the projection dropped -----------------------------------------------------------
def test_o07_clickhouse_down_holds_the_segment_serves_the_app_and_ships_once_after(workdir):
    with ow.observe_trip(workdir) as trip:
        alpha = trip.world.alpha
        ids = [ow.served(trip, alpha, "o07-before")]
        ow.captured(trip, alpha, ids, CONTENT)
        fault = harness.signal_container("clickhouse")
        try:
            report = ow.shipped(trip)
            assert report.shipped == 0 and len(report.held) == 1, report
            ids.append(ow.app_serves(trip, alpha, "o07-clickhouse-down"))
        finally:
            fault.revert()
            harness.wait_clickhouse()
        report = ow.shipped(trip)
        assert (report.shipped, report.rows) == (1, 1), report
        assert trip.traces.rows("trace_envelopes", ids[0]) == 1
        assert ow.shipped(trip).rows == 0
        for request_id in ids:
            world.settled_once(trip, request_id)
        trip.conserved(alpha)


# --- o09 a worker restarted ---------------------------------------------------------------
class Killed(Exception):
    """The projector process dying (in-process stand-in for SIGKILL between two calls)."""


class DiesAfterInsert:
    """The projector's outbox, whose process dies after the ClickHouse insert, before the ack."""

    def __init__(self, outbox) -> None:
        self.outbox = outbox

    async def pending(self, **kw):
        return await self.outbox.pending(**kw)

    async def acknowledge(self, *args, **kw):
        raise Killed("after the insert")


def test_o09_a_projector_killed_after_its_insert_redelivers_and_projects_once(workdir):
    from infrx.state.jobstore import connector
    from infrx.traces.feedback import FeedbackProjector
    from infrx.traces.feedback.pg import PgFeedbackOutbox
    with ow.observe_trip(workdir, start=("worker",)) as trip:
        ow.sql(trip, "insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                     "('feedback', true, 'e5l', 'E5L o09') on conflict (name) do update set "
                     "enabled = true")
        trip.box.start("gateway", FEEDBACK_API="1")
        alpha = trip.world.alpha
        request_id = ow.served(trip, alpha, "o09")
        answer = trip.http.post("/v1/feedback", headers=trip.headers(alpha, "e5l-o09"), json={
            "request_id": request_id, "name": "rating", "value": 5})
        assert answer.status_code == 201, answer.text
        outbox = PgFeedbackOutbox(connector(trip.traces.dsn))
        feedback = trip.traces.retention.feedback
        try:
            run(FeedbackProjector(DiesAfterInsert(outbox), feedback).pump())
            raise AssertionError("premise: the first projector died after its insert")
        except Killed:
            pass
        assert run(FeedbackProjector(outbox, feedback).pump())["read"] == 0, \
            "redelivered inside the claim window"
        world.set_clock(trip.world.database, 31.0)
        assert run(FeedbackProjector(outbox, feedback).pump())["acknowledged"] == 1
        world.set_clock(trip.world.database, 0.0)
        assert len(run(feedback.find(alpha.org_id, request_id))) == 1, "projected twice"
        assert run(outbox.lag())["pending"] == 0


def test_o09_a_box_worker_killed_mid_traffic_restarts_and_finishes_every_job_once(workdir):
    with ow.observe_trip(workdir, start=("gateway",)) as trip:
        alpha = trip.world.alpha
        accepted = trip.send(alpha, "async", world.TEXT, "e5l-o09-queued")
        assert accepted.status_code == 202, accepted.text
        trip.box.start("worker")
        trip.box.kill("worker")
        time.sleep(1.0)
        trip.box.start("worker")
        # The premise, before anything waits: the first worker may finish the job before its
        # kill, and then only the sync call below would notice a missing worker - by the
        # client's 120 s ReadTimeout, not an assertion (0-LO3-RV-1).
        worker = trip.box.processes.get("worker")
        assert worker is not None and worker.poll() is None, "premise: the worker came back"
        request_id = accepted.json()["request_id"]
        # A kill after the first worker claimed a phase leaves the job waiting for that lease to
        # expire (inference 120 s, preparation 30 s) before the new worker may claim it: the
        # deadline covers both leases plus the served run, or the case races the TTL (0-F2).
        from infrx.contracts.limits import DEFAULTS
        leases = DEFAULTS.lease_ttl_s + DEFAULTS.preparation_lease_ttl_s
        assert world.terminal(trip, request_id, timeout=leases + 90.0) == "succeeded"
        world.settled_once(trip, request_id)
        ow.app_serves(trip, alpha, "o09-after-restart")
        trip.conserved(alpha)
