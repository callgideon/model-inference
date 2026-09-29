"""WR-C6-CAPTURE (lab-capture): trace capture behind `TRACE_PUMPS`, on fakes and a real spool.

    uv run --frozen pytest -q tests/t/capture

(a) the consent source: a key's own opt-in (`api_keys.trace_mode`, null = off) under its
organization's consent head (`infrx.consent_history`), read at request time, cached per
process for a bounded TTL, fail closed; the ingress hands admission that policy instead of
`off_mode_policy` only when the capture is composed.

Each case names the defect it catches (its oracle); `mutants.py` kills each decision.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from infrx.contracts.records import ConsentSnapshot, TraceMode
from infrx.gateway import capture

from ...g import support

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=1)
AUTH = SimpleNamespace(org_id=support.ORG, key_id=support.KEY)


def run(coroutine):
    return asyncio.run(coroutine)


def row(key="full", org="full", *, version=2, days=30, evaluation=True, effective=EARLIER,
        revoked=None):
    """What `CONSENT_SQL` answers: the key's mode, then the org's consent head (all None
    when the org has no consent row)."""
    if org is None:
        return (key, None, None, None, None, None, None)
    return (key, version, org, days, evaluation, effective, revoked)


class Reads:
    """A consent reader that answers `rows` in turn and counts the reads."""

    def __init__(self, *rows, fail=False) -> None:
        self.rows, self.fail, self.calls = list(rows), fail, []

    async def __call__(self, org_id, key_id):
        self.calls.append((org_id, key_id))
        if self.fail:
            raise OSError("postgres did not answer")
        return self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]


def source(reads, clock=lambda: 0.0, **kw):
    consent = capture.ConsentSource(None, clock=clock, **kw)
    consent.read = reads
    return consent


# --- (a) the consent source -----------------------------------------------------------
def test_a_consented_key_under_a_consenting_org_captures_at_the_lower_of_the_two():
    """Oracle: the key's opt-in ignored (the org's mode wins), the org's consent ignored
    (the key's wins), or `max` for `min` - each raises capture above what one side
    consented to."""
    cases = {("full", "full"): TraceMode.full, ("minimal", "full"): TraceMode.minimal,
             ("full", "minimal"): TraceMode.minimal, ("off", "full"): TraceMode.off,
             ("full", "off"): TraceMode.off}
    for (key, org), want in cases.items():
        got = run(source(Reads(row(key, org))).policy(AUTH, NOW))
        assert got.trace_mode is want, (key, org, got.trace_mode)
        assert got.org_id == support.ORG


def test_a_key_that_never_opted_in_or_an_org_without_consent_is_off():
    """Oracle: a null key mode read as the org's (opt-out instead of opt-in), or a missing
    consent row read as consent."""
    for answer in (row(None, "full"), row("full", None), None):
        got = run(source(Reads(answer)).policy(AUTH, NOW))
        assert got == capture.off_mode_policy(support.ORG, NOW), answer


def test_a_revoked_or_not_yet_effective_consent_is_off():
    """Oracle: the revocation or the effective time ignored - capture under a consent that
    is not in force."""
    for answer in (row(revoked=NOW - timedelta(seconds=1)), row(effective=NOW + timedelta(1))):
        got = run(source(Reads(answer)).policy(AUTH, NOW))
        assert got.trace_mode is TraceMode.off, answer


def test_the_policy_carries_the_consent_head_and_evaluation_only_at_full():
    """Oracle: a snapshot that loses its version/retention (the job would pin consent 0),
    or evaluation consent kept after the key lowered the mode below full."""
    full = run(source(Reads(row(version=7, days=14))).policy(AUTH, NOW))
    assert (full.consent_version, full.content_retention_days, full.evaluation_consent,
            full.effective_at) == (7, 14, True, EARLIER)
    minimal = run(source(Reads(row("minimal", "full"))).policy(AUTH, NOW))
    assert minimal.evaluation_consent is False


def test_consent_is_read_once_per_key_within_the_ttl_and_again_after_it():
    """Oracle: no cache (a database read per request), a cache keyed by the org only (one
    key's opt-in answers for another), or no expiry (a revocation never lands)."""
    clock = [0.0]
    reads = Reads(row("full", "full"), row("full", "full"), row("full", "off"))
    consent = source(reads, clock=lambda: clock[0])
    other = SimpleNamespace(org_id=support.ORG, key_id=support.IDS.provider_dev_key)
    assert run(consent.policy(AUTH, NOW)).trace_mode is TraceMode.full
    assert run(consent.policy(AUTH, NOW)).trace_mode is TraceMode.full
    run(consent.policy(other, NOW))
    assert reads.calls == [(support.ORG, support.KEY), (support.ORG, other.key_id)]
    clock[0] = capture.CONSENT_TTL_S + 1
    assert run(consent.policy(AUTH, NOW)).trace_mode is TraceMode.off
    assert len(reads.calls) == 3


def test_the_consent_cache_is_bounded():
    """Oracle: an unbounded cache - key ids are the tenant's to mint."""
    consent = source(Reads(row()), max_entries=2)
    for n in range(5):
        run(consent.policy(SimpleNamespace(org_id=support.ORG, key_id=f"k{n}"), NOW))
    assert len(consent.cache) == 2


def test_a_consent_read_that_fails_is_off_and_never_raises():
    """Oracle: a failure that raises into the request (inference would depend on the trace
    path) or reads as consent."""
    got = run(source(Reads(fail=True)).policy(AUTH, NOW))
    assert got == capture.off_mode_policy(support.ORG, NOW)


def test_the_runtime_login_without_a_grant_reads_off():
    """0021's `infrx_runtime` has no read of `consent_history` or `api_keys` (a grant would
    be a migration): the source then fails closed to off, it does not raise. The failure is
    the one `pg_rows` maps every psycopg error to."""
    from infrx.contracts import errors

    async def refused(connect, sql, params):
        raise errors.DependencyUnavailable("postgres: InsufficientPrivilege")
    consent = capture.ConsentSource("runtime-login", clock=lambda: 0.0)
    consent.rows = refused
    assert run(consent.policy(AUTH, NOW)) == capture.off_mode_policy(support.ORG, NOW)


def test_the_sql_reads_the_key_of_its_own_org_and_the_consent_head():
    """Oracle (with the PostgreSQL case in test_capture_pg.py): the statement's binding in
    the wrong order, or the head chosen among unrevoked rows only (a revoked head would fall
    back to an older consent instead of off)."""
    calls = []

    async def rows(connect, sql, params):
        calls.append((sql, params))
        return [row()]
    consent = capture.ConsentSource("connect", clock=lambda: 0.0)
    consent.rows = rows
    assert run(consent.read(support.ORG, support.KEY)) == row()
    [(sql, params)] = calls
    assert params == (support.KEY, support.ORG)
    assert "order by h.consent_version desc limit 1" in sql and "revoked_at is null" not in \
        sql.split("left join")[1]


# --- (a) the ingress seam ---------------------------------------------------------------
class Policy:
    """A composed capture whose policy is `mode` for every request."""

    def __init__(self, mode: TraceMode) -> None:
        self.mode, self.asked = mode, []

    async def policy(self, auth, now) -> ConsentSnapshot:
        self.asked.append((auth.org_id, auth.key_id, now))
        return ConsentSnapshot(org_id=auth.org_id, consent_version=3, trace_mode=self.mode,
                               content_retention_days=30, evaluation_consent=False,
                               effective_at=now)

    def response(self, accepted, request, headers):
        return accepted


def test_the_ingress_admits_with_the_composed_capture_policy():
    """Oracle: the ingress keeps `off_mode_policy` although a capture is composed (o01's
    NOT RUN: nothing a served request does reaches a spool), or asks for another tenant."""
    calls, accept = support.recorder()
    composed = Policy(TraceMode.full)
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept, capture=composed))
    answer = TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                  json={**support.BODY})
    assert answer.status_code == 202, answer.text
    policy = calls[0][1].trace_policy
    assert (policy.trace_mode, policy.consent_version) == (TraceMode.full, 3)
    assert composed.asked[0][:2] == (support.ORG, support.KEY)
    assert composed.asked[0][2] == calls[0][1].created_at


def test_without_a_composed_capture_the_ingress_policy_is_off():
    """Oracle: the switch-off path changed (E4's launched behaviour)."""
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    assert TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                json=support.BODY).status_code == 202
    assert calls[0][1].trace_policy.trace_mode is TraceMode.off


# --- (b) the request-path capture hook ---------------------------------------------------
# A consented sync or SSE request writes ONE record to the gateway's spool: the request
# (redacted: inline media by digest, the caller's credential never) and the answer as the
# client received it. Off, nothing; `minimal`, metadata only; async, the worker's (c).
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.responses import Response  # noqa: E402

from infrx.traces.spool import SpoolTraceSink, recover  # noqa: E402

ANSWER = {"id": "chatcmpl-1", "choices": [{"index": 0, "message": {
    "role": "assistant", "content": f"echo {support.TOKEN} the van is red"}}]}
FRAMES = (b'data: {"choices":[{"delta":{"content":"the van"}}]}\n\n',
          b'data: {"choices":[{"delta":{"content":" is red"}}]}\n\n', b"data: [DONE]\n\n")


class Streamed(Response):
    """An SSE answer as the relay sends it: its own `__call__`, frame by frame."""

    def __init__(self) -> None:
        self.status_code, self.background = 200, None
        self.init_headers({"content-type": "text/event-stream"})

    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
        for frame in FRAMES:
            await send({"type": "http.response.body", "body": frame, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def answered(stream: bool = False):
    """An `accept` answering like the relay: the JSON result, or SSE frames."""
    calls = []

    async def accept(auth, request, idem):
        calls.append(request)
        if request.execution_mode.value == "async":
            return JSONResponse({"request_id": request.request_id}, status_code=202)
        return Streamed() if stream else JSONResponse(ANSWER)
    return calls, accept


def gateway(tmp_path, mode: TraceMode, stream: bool = False):
    """(client, sink, calls): the ingress with a capture composed over a real spool."""
    calls, accept = answered(stream)
    sink = SpoolTraceSink(capture.Wall, spool_dir=tmp_path / "spool")
    composed = capture.GatewayCapture(Policy(mode), sink, None, tmp_path / "spool")
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept, capture=composed))
    return TestClient(app), composed, calls


def spooled(tmp_path, composed):
    """What reached the spool, once the capture closed (flushed and sealed)."""
    run(composed.close())
    return recover(tmp_path / "spool")


def said(text: str) -> dict:
    return {**support.BODY, "messages": [{"role": "user", "content": text}]}


def test_a_consented_sync_request_writes_one_record_of_the_request_and_its_answer(tmp_path):
    """Oracle: nothing captured although consent is on (o01's gap), a second record for one
    request, the answer missing, or the record under another org, key or mode."""
    client, composed, calls = gateway(tmp_path, TraceMode.full)
    answer = client.post(support.CHAT_PATH, headers=support.AUTH, json=said("describe the van"))
    assert answer.status_code == 200 and answer.json() == ANSWER
    scan = spooled(tmp_path, composed)
    assert len(scan.records) == 1, scan.records
    [envelope], [content] = scan.records, scan.contents
    request = calls[0]
    assert (envelope.request_id, envelope.org_id, envelope.key_id, envelope.mode) == \
        (request.request_id, support.ORG, support.KEY, TraceMode.full)
    assert envelope.content_complete and envelope.content_bytes == len(content)
    head, _, body = content.partition(b"\n")
    assert b"describe the van" in head and b"the van is red" in body
    assert envelope.completed_at is not None and envelope.started_at == request.created_at


def test_an_unconsented_request_writes_nothing(tmp_path):
    """Oracle: capture that ignores the policy - content stored for an org that never
    opted in, which deleting afterwards cannot undo - or an off request still wrapped (the
    request line serialized on the path of every unconsented request)."""
    client, composed, calls = gateway(tmp_path, TraceMode.off)
    assert client.post(support.CHAT_PATH, headers=support.AUTH,
                       json=said("private")).status_code == 200
    accepted = JSONResponse(ANSWER)
    assert composed.response(accepted, calls[0], {}) is accepted
    scan = spooled(tmp_path, composed)
    assert scan.records == [] and scan.segments == 0


def test_a_minimal_request_writes_metadata_only(tmp_path):
    """Oracle: content stored under a `minimal` consent (R12)."""
    client, composed, _ = gateway(tmp_path, TraceMode.minimal)
    assert client.post(support.CHAT_PATH, headers=support.AUTH,
                       json=said("describe the van")).status_code == 200
    scan = spooled(tmp_path, composed)
    assert len(scan.records) == 1, scan.records
    [envelope] = scan.records
    assert envelope.mode is TraceMode.minimal and scan.contents == [b""]
    assert not envelope.content_complete and envelope.content_ref is None


def test_a_credential_never_reaches_the_spool(tmp_path):
    """Oracle: the bearer token written to disk - from the request headers, echoed in the
    prompt, or echoed back in the answer."""
    client, composed, _ = gateway(tmp_path, TraceMode.full)
    assert client.post(support.CHAT_PATH, headers=support.AUTH,
                       json=said(f"my key is {support.TOKEN}")).status_code == 200
    run(composed.close())
    spool = b"".join(path.read_bytes() for path in (tmp_path / "spool").iterdir()
                     if path.is_file())
    assert spool and support.TOKEN.encode() not in spool
    assert b"authorization" not in spool.lower()
    assert spool.count(capture.REDACTED) == 2


def test_a_credential_repeated_in_one_part_is_scrubbed_everywhere(tmp_path):
    """Oracle (0-LC-R1): a scrub of the first occurrence only - the token said twice in one
    prompt, and echoed in the answer, leaks from the second."""
    client, composed, _ = gateway(tmp_path, TraceMode.full)
    assert client.post(support.CHAT_PATH, headers=support.AUTH, json=said(
        f"my key is {support.TOKEN} yes {support.TOKEN}")).status_code == 200
    run(composed.close())
    spool = b"".join(path.read_bytes() for path in (tmp_path / "spool").iterdir()
                     if path.is_file())
    assert spool and support.TOKEN.encode() not in spool
    assert spool.count(capture.REDACTED) == 3


def test_a_remote_media_url_is_spooled_without_its_query_or_credentials():
    """Oracle (0-LC-R2): the customer's URL verbatim - a presigned query string (a
    credential) or `user:pass@` - in the spool, S3 and ClickHouse (S2M D1: durable records
    hold our reference, never the customer's URL)."""
    url = "https://me:pw@bucket.s3.amazonaws.com/clip.mp4?X-Amz-Signature=deadbeef#t=1"
    part = {"type": "video_url", "video_url": {"url": url}}
    got = capture.redacted({"messages": [{"role": "user", "content": [part]}]})
    assert got["messages"][0]["content"][0]["video_url"]["url"] == \
        "https://bucket.s3.amazonaws.com/clip.mp4"
    assert capture.redacted({"content": "see https://x.test/a?b=c"}) == \
        {"content": "see https://x.test/a?b=c"}


def test_an_idempotent_replay_writes_no_second_record(tmp_path):
    """Oracle (0-LC-R3): a replayed answer (the original job's `Inference-Id`) recorded again
    under the fresh request id - a second record that names no job and ships unpinned."""
    from infrx.contracts import wire
    _, composed, _ = gateway(tmp_path, TraceMode.full)
    request = SimpleNamespace(trace_policy=SimpleNamespace(trace_mode=TraceMode.full),
                              execution_mode=None)
    replay = JSONResponse(ANSWER, headers={wire.HEADER_INFERENCE_ID: "req_original_job",
                                           wire.HEADER_IDEMPOTENCY_REPLAYED: "true"})
    assert composed.response(replay, request, support.AUTH) is replay
    fresh = JSONResponse(ANSWER)
    assert composed.response(fresh, request, support.AUTH) is not fresh


def test_inline_media_is_spooled_by_digest_not_bytes():
    """Oracle: the base64 clip itself in the trace (up to 96 MiB on the request path)."""
    import hashlib
    url = "data:video/mp4;base64," + "A" * 64
    part = {"type": "video_url", "video_url": {"url": url}}
    got = capture.redacted({"messages": [{"role": "user", "content": [part]}]})
    assert got["messages"][0]["content"][0]["video_url"]["url"] == \
        "data-sha256:" + hashlib.sha256(url.encode()).hexdigest()


def test_a_streamed_answer_is_captured_as_relayed(tmp_path):
    """Oracle: only the first frame kept, or the stream's frames lost because the answer
    is not one JSON body."""
    client, composed, _ = gateway(tmp_path, TraceMode.full, stream=True)
    answer = client.post(support.CHAT_PATH, headers=support.AUTH,
                         json={**said("describe the van"), "stream": True})
    assert answer.status_code == 200 and answer.content == b"".join(FRAMES)
    contents = spooled(tmp_path, composed).contents
    assert len(contents) == 1, contents
    [content] = contents
    assert content.partition(b"\n")[2] == b"".join(FRAMES)


def test_an_async_request_is_left_to_the_worker(tmp_path):
    """Oracle: the gateway spools the 202 (a record with no output) and the worker spools
    the job's output too: two records for one request (the ruling (c) split)."""
    client, composed, _ = gateway(tmp_path, TraceMode.full)
    answer = client.post(support.CHAT_PATH, headers={**support.AUTH, "prefer": "respond-async"},
                         json=said("describe the van"))
    assert answer.status_code == 202, answer.text
    assert spooled(tmp_path, composed).records == []


def test_a_capture_that_cannot_keep_the_record_never_fails_the_request(tmp_path):
    """Oracle: a trace failure (a closed spool, a finish that raises) that becomes the
    request's error."""
    client, composed, _ = gateway(tmp_path, TraceMode.full)
    run(composed.close())
    answer = client.post(support.CHAT_PATH, headers=support.AUTH, json=said("describe"))
    assert answer.status_code == 200 and answer.json() == ANSWER
    opened = composed.sink.open

    def failing(*args, **kw):
        capture_ = opened(*args, **kw)

        async def finish(envelope):
            raise RuntimeError("the spool writer died")
        capture_.finish = finish
        return capture_
    composed.sink.open = failing
    answer = client.post(support.CHAT_PATH, headers=support.AUTH, json=said("describe"))
    assert answer.status_code == 200 and answer.json() == ANSWER


def test_a_capture_that_cannot_open_never_fails_the_request(tmp_path):
    """Oracle: a sink that raises on `open` turns the trace path into the request's 500."""
    client, composed, _ = gateway(tmp_path, TraceMode.full)

    def broken(*args, **kw):
        raise OSError("the spool is gone")
    composed.sink.open = broken
    answer = client.post(support.CHAT_PATH, headers=support.AUTH, json=said("describe"))
    assert answer.status_code == 200 and answer.json() == ANSWER
    run(composed.close())


def test_an_answer_that_breaks_off_is_recorded_as_incomplete(tmp_path):
    """Oracle: a stream cut after its first frame (a relay failure, the process stopping)
    recorded `content_complete` - a partial capture must never look complete (R27)."""
    import pytest
    client, composed, calls = gateway(tmp_path, TraceMode.full, stream=True)
    assert client.post(support.CHAT_PATH, headers=support.AUTH,
                       json={**said("describe the van"), "stream": True}).status_code == 200

    class Broken(Streamed):
        async def __call__(self, scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": FRAMES[0], "more_body": True})
            raise ConnectionError("the relay stopped")

    async def sent(message):
        pass
    wrapped = composed.response(Broken(), calls[0], {})
    with pytest.raises(ConnectionError):
        run(wrapped({"type": "http"}, None, sent))
    first, cut = spooled(tmp_path, composed).records
    assert first.content_complete and not cut.content_complete
    assert cut.content_bytes > 0 and cut.request_id == first.request_id


# --- (c) async output in the worker, shipped by the gateway ------------------------------
# Proposed ruling (c): the worker writes an async job's record into a spool of its own,
# `TRACE_SPOOL_DIR/jobs/<job id>-<n>` (hidden while it writes, renamed when sealed: one
# writer per directory, the sink's lock); only the gateway ships (every `SHIP_S` in its
# lifespan): its own spool, then each finished job spool it can lock, removed once acked.
import pytest  # noqa: E402

from infrx.contracts import errors  # noqa: E402
from infrx.traces import ship  # noqa: E402

from ..ship.test_ship import MemoryProjection, Objects  # noqa: E402

TEXT = "the van is red"


class Store:
    """The worker's job store as the runner sees it: `load_work`, `complete`, `put_result`."""

    def __init__(self, request, *, refuse: bool = False) -> None:
        self.request, self.refuse, self.results = request, refuse, []

    async def load_work(self, lease):
        return SimpleNamespace(request=self.request)

    async def put_result(self, job_id, text, lease=None):
        self.results.append((job_id, text))
        return f"infrx-result:{job_id}"

    async def complete(self, lease, outcome):
        if self.refuse:
            raise errors.StaleLease("another worker holds the lease")
        return "settled"

    async def heartbeat(self, lease):
        return "beat"


def admitted(tmp_path, mode: TraceMode, prefer_async: bool = True):
    """A request as the ingress admitted it (its policy `mode`)."""
    client, composed, calls = gateway(tmp_path / "gw", mode)
    headers = {**support.AUTH, **({"prefer": "respond-async"} if prefer_async else {})}
    client.post(support.CHAT_PATH, headers=headers, json=said("describe the van"))
    run(composed.close())
    return calls[0]


def worked(tmp_path, request, *, text=TEXT, root=None, **kw):
    """One attempt through the worker's capture: load, (result,) complete. The runner."""
    store = Store(request, **kw)
    runner = SimpleNamespace(jobs=store, put_result=store.put_result)
    capture.capture_jobs(runner, root or tmp_path / "spool", capture.Wall)
    lease = SimpleNamespace(job_id=request.request_id)

    async def attempt():
        await runner.jobs.load_work(lease)
        if text is not None:
            assert await runner.put_result(request.request_id, text, lease) == \
                f"infrx-result:{request.request_id}"
        return await runner.jobs.complete(lease, "outcome")
    return run(attempt()), runner, store


def job_spools(tmp_path):
    jobs = tmp_path / "spool" / capture.JOBS_DIR
    return sorted(jobs.iterdir()) if jobs.is_dir() else []


def test_an_async_jobs_output_is_spooled_by_the_worker_under_its_job_id(tmp_path):
    """Oracle: the async output never captured (the worker drops it), written into the
    gateway's directory (two writers), or left hidden (never shipped)."""
    request = admitted(tmp_path, TraceMode.full)
    settled, _, store = worked(tmp_path, request)
    assert settled == "settled" and store.results == [(request.request_id, TEXT)]
    spools = job_spools(tmp_path)
    assert len(spools) == 1, spools
    [spool] = spools
    assert spool.name.startswith(f"{request.request_id}-")
    scan = recover(spool)
    [envelope], [content] = scan.records, scan.contents
    assert (envelope.request_id, envelope.org_id, envelope.mode) == \
        (request.request_id, support.ORG, TraceMode.full)
    assert envelope.content_complete and content == request_line(request) + TEXT.encode()
    assert not list((tmp_path / "spool").glob("trace-*"))        # never the gateway's own


def request_line(request):
    return capture.request_line(request)


def test_the_worker_spools_only_consented_async_jobs(tmp_path):
    """Oracle: a sync job spooled by the worker too (the gateway already has its record:
    two for one request), or an unconsented job's output kept."""
    worked(tmp_path, admitted(tmp_path / "sync", TraceMode.full, prefer_async=False))
    worked(tmp_path, admitted(tmp_path / "off", TraceMode.off))
    assert job_spools(tmp_path) == []


def test_a_job_without_output_is_recorded_as_incomplete(tmp_path):
    """Oracle: a failed job (no result) recorded as complete, or not recorded at all."""
    request = admitted(tmp_path, TraceMode.full)
    worked(tmp_path, request, text=None)
    [spool] = job_spools(tmp_path)
    [envelope] = recover(spool).records
    assert not envelope.content_complete and envelope.request_id == request.request_id


def test_a_minimal_async_job_is_recorded_metadata_only(tmp_path):
    """Oracle (lens R6, merge #54): a `minimal` async job's output stored as content (R12),
    or no record at all for it (the worker keeping `full` jobs only)."""
    request = admitted(tmp_path, TraceMode.minimal)
    worked(tmp_path, request)
    spools = job_spools(tmp_path)
    assert len(spools) == 1, spools
    scan = recover(spools[0])
    assert len(scan.records) == 1, scan.records
    envelope = scan.records[0]
    assert (envelope.request_id, envelope.mode) == (request.request_id, TraceMode.minimal)
    assert scan.contents == [b""] and envelope.content_ref is None


def test_a_refused_completion_spools_nothing_and_raises_as_before(tmp_path):
    """Oracle: a record for an attempt that lost its lease (the winner writes its own), or
    the refusal swallowed by the capture."""
    request = admitted(tmp_path, TraceMode.full)
    with pytest.raises(errors.StaleLease):
        worked(tmp_path, request, refuse=True)
    assert job_spools(tmp_path) == []


def test_a_worker_capture_failure_never_fails_the_job(tmp_path):
    """Oracle: a spool that cannot be written (the root is a file) fails the settlement."""
    request = admitted(tmp_path, TraceMode.full)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    settled, _, _ = worked(tmp_path, request, root=blocked)
    assert settled == "settled"


def test_the_worker_remembers_a_bounded_number_of_jobs(tmp_path):
    """Oracle: an attempt that never completes (killed, lease lost) held for ever."""
    request = admitted(tmp_path, TraceMode.full)
    store = Store(request)
    runner = SimpleNamespace(jobs=store, put_result=store.put_result)
    capture.capture_jobs(runner, tmp_path / "spool", capture.Wall, remember=3)
    for n in range(5):
        run(runner.jobs.load_work(SimpleNamespace(job_id=f"job-{n}")))
    assert list(runner.jobs.open) == ["job-2", "job-3", "job-4"]
    assert run(runner.jobs.heartbeat(None)) == "beat"          # everything else is the store's


def shipping(tmp_path, mode=TraceMode.full):
    """(client, composed, projection): the gateway composed with a shipper over memory."""
    client, composed, calls = gateway(tmp_path, mode)
    projection, objects = MemoryProjection(), Objects()
    composed.shipper = ship.Shipper(composed.sink, projection, objects)
    return client, composed, projection, calls


def test_the_gateway_ships_its_own_spool_and_every_finished_job_spool(tmp_path):
    """Oracle: the job spools never shipped (async traces never reach the projection), the
    gateway's own tail never sealed, or a shipped job spool left on disk."""
    client, composed, projection, calls = shipping(tmp_path)
    assert client.post(support.CHAT_PATH, headers=support.AUTH,
                       json=said("describe")).status_code == 200
    job = admitted(tmp_path, TraceMode.full)
    worked(tmp_path, job)
    run(composed.ship_once())
    shipped = {(row.request_id, row.content_stored) for row in projection.inserted}
    assert shipped == {(calls[0].request_id, True), (job.request_id, True)}
    assert job_spools(tmp_path) == []
    assert composed.sink.segments() == ()
    run(composed.ship_once())
    assert len(projection.inserted) == 2, "a second pass re-shipped"
    run(composed.close())


def test_a_job_spool_still_being_written_is_left_to_its_writer(tmp_path):
    """Oracle: the gateway ships a directory whose writer still holds it (acking a segment
    being appended), or a hidden one the worker has not sealed; or one spool it cannot open
    stops every other spool from shipping."""
    client, composed, projection, _ = shipping(tmp_path)
    jobs = tmp_path / "spool" / capture.JOBS_DIR
    held = SpoolTraceSink(capture.Wall, spool_dir=jobs / "held-1")
    hidden = jobs / ".hidden-1"
    hidden.mkdir()
    bad = jobs / "0bad-1"                    # sorts first; this process cannot open it
    bad.mkdir()
    bad.chmod(0)
    job = admitted(tmp_path, TraceMode.full)
    worked(tmp_path, job)
    try:
        run(composed.ship_once())
    finally:
        bad.chmod(0o700)
    assert (jobs / "held-1").is_dir() and hidden.is_dir() and bad.is_dir()
    assert [row.request_id for row in projection.inserted] == [job.request_id]
    run(held.close())
    run(composed.close())


def test_the_pump_ships_every_interval_until_stopped(tmp_path):
    """Oracle: a lifespan that never ships (the o01 gap: records sit in the spool), or a
    pump that outlives shutdown, or one failed pass ending shipping for the process."""
    client, composed, projection, _ = shipping(tmp_path)
    passes = []

    async def once():
        passes.append(1)
        await asyncio.sleep(0)
        if len(passes) == 1:
            raise OSError("clickhouse did not answer")
    composed.ship_once = once

    async def lifetime():
        stop = asyncio.Event()
        task = asyncio.create_task(composed.pump(stop, every_s=0.001))
        for _ in range(5000):                     # bounded: a pump that died fails, not hangs
            if len(passes) >= 3 or task.done():
                break
            await asyncio.sleep(0.001)
        stop.set()
        await asyncio.wait_for(task, 1.0)
    run(lifetime())
    assert len(passes) >= 3
    run(composed.close())


# --- (d) the switch: TRACE_PUMPS composes the capture into the gateway ---------------------
from infrx.config import RuntimeMisconfigured  # noqa: E402
from infrx.gateway import pilot  # noqa: E402

TRACE_SETTINGS = {"clickhouse_url": "http://ch.invalid:8123/infrx",
                  "s3_trace_bucket": "infrx-traces"}


def switched(tmp_path, on: bool = True, **pilot_settings):
    config = support.settings(**{"trace_spool_dir": str(tmp_path / "spool"),
                                 **TRACE_SETTINGS, **pilot_settings})
    config.deployment = config.deployment.replace(trace_pumps=on,
                                                  s3_endpoint_url="http://127.0.0.1:9")
    return config


def buildable(monkeypatch):
    """`build`'s two collaborators that would connect (ClickHouse, C2's refs) recorded."""
    from infrx.worker import __main__ as worker_main
    built = {}

    def shipper(limits, spool, **kw):
        built.update(limits=limits, spool=spool, **kw)
        return SimpleNamespace(spool=spool)
    monkeypatch.setattr(ship, "build_shipper", shipper)
    monkeypatch.setattr(worker_main, "content_holds", lambda mode, connect: ("holds", connect))
    return built


def test_trace_pumps_off_composes_no_capture(tmp_path):
    """Oracle: the switch-off gateway builds a spool or a consent reader (E4's launched
    process would lock a directory and read consent it never uses)."""
    assert capture.adapters(switched(tmp_path, on=False), "connect") == {}
    assert not (tmp_path / "spool").exists()


@pytest.mark.parametrize("unset", ["trace_spool_dir", "clickhouse_url", "s3_trace_bucket"])
def test_trace_pumps_on_refuses_without_its_settings(unset, tmp_path):
    """Oracle: a switch that silently captures nowhere (or ships nowhere)."""
    with pytest.raises(RuntimeMisconfigured) as refused:
        capture.adapters(switched(tmp_path, **{unset: ""}), "connect")
    assert unset.upper() in str(refused.value)


def test_trace_pumps_on_composes_consent_spool_and_shipper(tmp_path, monkeypatch):
    """Oracle: the consent read off the job store's pool, a spool elsewhere than
    TRACE_SPOOL_DIR, the shipper without C2's holds or the deployment's endpoint."""
    built = buildable(monkeypatch)
    composed = capture.adapters(switched(tmp_path), "connect")["capture"]
    assert composed.consent.connect == "connect"
    assert composed.sink.spool_dir == tmp_path / "spool" and composed.shipper.spool is composed.sink
    assert built.get("holds") == ("holds", "connect")
    assert built["endpoint_url"] == "http://127.0.0.1:9"
    assert built["limits"].clickhouse_url == TRACE_SETTINGS["clickhouse_url"]
    run(composed.close())


def test_one_gateway_process_per_spool_directory(tmp_path, monkeypatch):
    """Oracle: a second gateway on the same TRACE_SPOOL_DIR (two writers: each adopts and
    acks the other's live segments)."""
    buildable(monkeypatch)
    first = capture.build(switched(tmp_path), "connect")
    with pytest.raises(RuntimeMisconfigured) as refused:
        capture.build(switched(tmp_path), "connect")
    assert "TRACE_SPOOL_DIR" in str(refused.value)
    run(first.close())


def test_the_pilot_composes_the_capture_and_its_lifespan_ships_then_closes(tmp_path):
    """Oracle: the capture built but never handed to the ingress (policy off), the lifespan
    that never runs the ship pump, or a shutdown that leaves the spool unflushed."""
    from ...g.test_composition import composed as pilot_composed
    from ...g.test_composition import rs, served
    events = []

    class Composed:
        async def policy(self, auth, now):
            return capture.off_mode_policy(auth.org_id, now)

        def response(self, accepted, request, headers):
            return accepted

        async def pump(self, stop):
            events.append("pump")
            await stop.wait()
            events.append("stopped")

        async def close(self):
            events.append("close")
    fake = Composed()
    rt, deps = pilot_composed(capture=fake)
    assert deps.capture is fake and rt.lifetime.capture is fake

    async def lifetime():
        async with pilot.lifespan(served(rt, deps)):
            for _ in range(1000):                 # bounded: a pump that never starts fails
                if "pump" in events:
                    break
                await asyncio.sleep(0)
    rs.run(lifetime())
    assert events == ["pump", "stopped", "close"]
    rt_off, deps_off = pilot_composed()
    assert deps_off.capture is None and rt_off.lifetime.capture is None


def test_a_job_spool_that_did_not_ship_stays_for_the_next_pass(tmp_path):
    """Oracle: a job spool removed although its segment was held (ClickHouse down): the
    record is gone before it reached the projection."""
    from ..ship.test_ship import Projection
    client, composed, projection, _ = shipping(tmp_path)
    composed.shipper.projection = down = Projection(MemoryProjection())
    down.down = True
    job = admitted(tmp_path, TraceMode.full)
    worked(tmp_path, job)
    run(composed.ship_once())
    assert len(job_spools(tmp_path)) == 1
    down.down = False
    run(composed.ship_once())
    assert job_spools(tmp_path) == [] and \
        [row.request_id for row in down.inner.inserted] == [job.request_id]
    run(composed.close())


def test_the_pilot_asks_the_switch_for_its_capture_adapters(tmp_path, monkeypatch):
    """Oracle: `adapters_from_env` never composes the capture (TRACE_PUMPS on, and still no
    consent, capture or ship), or composes it off another pool."""
    from infrx.media.store import InMemoryObjectStore
    asked = []

    def adapters(settings, connect):
        asked.append((settings, connect))
        return {"capture": "the capture"}
    monkeypatch.setattr(pilot.trace_capture, "adapters", adapters)
    config = switched(tmp_path)
    built = pilot.adapters_from_env(config, objects=InMemoryObjectStore())
    assert built["capture"] == "the capture"
    [(settings, connect)] = asked
    assert settings is config and connect is built["jobs"]._connect
