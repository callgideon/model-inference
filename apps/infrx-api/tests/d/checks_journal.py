"""D4: the stream journal (0017) as SQL-level checks, on D2's "admission" scenario (R32/R40:
each is a migration mutant's named check; `tests/d/test_journal.py` runs them).

Every call is the boundary with the arguments `PgStreamStore` builds (`_append_args`), as the
owner, inside a transaction the check rolls back, so checks never see each other's rows.
Requests are stamped by a gateway an hour BEHIND the database clock
(`checks_leases.GATEWAY_SKEW`) and the database clock is moved explicitly, so an instant
taken from anything but `infrx.now()` shows. Byte counts are the stored measure,
`octet_length(payload::text)`, read from the database (`size`), never computed here.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

from psycopg.types.json import Jsonb

from infrx.contracts.codec import compact_bytes
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (Chunk, ChunkEventType, EngineEvent, JobState, LeaseKind,
                                     SettlementState, TerminalCause)
from infrx.state.journal import PgStreamStore

from . import checks_admission as ca
from . import checks_leases as cl
from .checks_dispatch import advance, claim, kinds, outcome, prepare

TTL, CHUNK_TTL = DEFAULTS.lease_ttl_s, DEFAULTS.journal_chunk_ttl_s
#: A store whose chunks live 30 s, so a prefix expires while the lease is still live.
SHORT = DEFAULTS.replace(journal_chunk_ttl_s=30)


# --------------------------------------------------------------------- driving
def args(lease, events, limits=DEFAULTS) -> dict:
    return PgStreamStore(None, limits=limits)._append_args(lease, tuple(events))


def append(conn, lease, events, limits=DEFAULTS):
    """(code, answer) of one `infrx.append` with the adapter's arguments."""
    return outcome(conn, "append", args(lease, events, limits))


def append_docs(conn, lease, docs, limits=DEFAULTS):
    """The same boundary with event documents the adapter could never build."""
    return outcome(conn, "append", {**args(lease, (), limits), "events": docs})


def at(generation: int, sequence: int) -> dict:
    return {"generation": generation, "sequence": sequence}


def read(conn, request, cursor=None, limit=100, *, org=None, handle=None):
    return outcome(conn, "read_journal", {
        "org_id": org or request.org_id, "limit": limit, "cursor": cursor,
        "job_handle": handle or cl.row(conn, request.request_id)["job_handle"]})


def expire(conn, now=None):
    return outcome(conn, "expire_journal", {"now": None if now is None else now.isoformat()})


def cancel(conn, request):
    return cl.d3(conn, "cancel", org_id=request.org_id,
                 job_handle=cl.row(conn, request.request_id)["job_handle"])


def event(payload: dict) -> EngineEvent:
    return EngineEvent(type=ChunkEventType.delta, payload=payload)


def db_now(conn):
    return conn.execute("select infrx.now()").fetchone()[0]


def size(conn, payload) -> int:
    """The measure 0017 stores and limits on: `octet_length(payload::text)`."""
    return conn.execute("select octet_length(%s::jsonb::text)", (Jsonb(payload),)).fetchone()[0]


def sized(conn, n: int) -> dict:
    """A payload of exactly `n` bytes on that measure."""
    payload = {"content": "x" * (n - size(conn, {"content": ""}))}
    assert size(conn, payload) == n, "the fixture payload is not the size asked for"
    return payload


def journal(conn, job_id: str) -> list[dict]:
    """The committed rows in cursor order, as `records.Chunk` fields."""
    cur = conn.execute("select job_id::text, generation, sequence, event_type, payload, bytes, "
                       "committed_at as persisted_at, expires_at from infrx.stream_chunks "
                       "where job_id = %s order by generation, sequence", (job_id,))
    names = [c.name for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def chunks(docs) -> list[Chunk]:
    return [Chunk.model_validate(doc) for doc in docs]


def cursors(rows) -> list[tuple[int, int, str]]:
    return [(r["generation"], r["sequence"], r["event_type"]) for r in rows]


def charged(conn) -> int:
    return conn.execute("select infrx.journal_bytes_charged()").fetchone()[0]


def running(conn, world, *, limits=DEFAULTS, worker: str = "w1", **kw):
    """Admitted with `limits` (the journal reservation is fixed at admission), prepared and
    claimed: (request, lease)."""
    request = cl.gateway_request(world, **kw)
    ca.admit(conn, request, b.idem(request, request.request_id), limits=limits)
    _, answer = claim(conn, request.request_id)
    assert prepare(conn, answer["lease"])[0] is None, "the fixture job did not queue"
    code, answer = cl.d3(conn, "claim", job_id=request.request_id, worker_id=worker)
    assert code is None, f"the fixture claim was refused: {code}"
    return request, cl.lease_of(answer)


def widest_terminal(conn) -> int:
    """The widest `{state, cause, settlement_state}` over every value of the vocabularies,
    on the stored measure (the fake's `check_terminal_capacity` bound)."""
    return conn.execute(
        "select max(octet_length(jsonb_build_object('state', s, 'cause', c, "
        "'settlement_state', t)::text)) from unnest(%s::text[]) s, unnest(%s::text[]) c, "
        "unnest(%s::text[]) t", ([v.value for v in JobState], [v.value for v in TerminalCause],
                                 [v.value for v in SettlementState])).fetchone()[0]


def one_terminal_last(conn, job_id: str, what: str, *, settlement: str | None = None) -> dict:
    """R30: exactly one terminal event, last in cursor order, derived from the STORED row,
    committed with the settlement, and counted in the job's stored bytes."""
    rows = journal(conn, job_id)
    terminals = [r for r in rows if r["event_type"] == "terminal"]
    assert len(terminals) == 1, f"{what}: {len(terminals)} terminal events in {cursors(rows)}"
    assert rows[-1]["event_type"] == "terminal", f"{what}: not last: {cursors(rows)}"
    job = cl.row(conn, job_id)
    want = {"state": job["state"], "cause": job["outcome_cause"],
            "settlement_state": settlement or job["settlement_state"]}
    assert terminals[0]["payload"] == want, f"{what}: {terminals[0]['payload']} != {want}"
    assert terminals[0]["bytes"] == size(conn, want), f"{what}: bytes not the stored measure"
    assert terminals[0]["persisted_at"] == job["settled_at"], \
        f"{what}: the terminal event is not the settlement's"
    assert job["journal_stored_bytes"] == sum(r["bytes"] for r in rows), \
        f"{what}: stored {job['journal_stored_bytes']} != the journal's {sum(r['bytes'] for r in rows)}"
    return terminals[0]


# --------------------------------------------------------------------- item 1: append
def check_append(conn) -> str:
    """DUR-OUTPUT: a batch through the fence is stored in ONE transaction and the answer is
    exactly the committed rows: cursors continue the generation across batches, `bytes` is
    the stored text's measure, `persisted_at` is the database clock AT THE APPEND and
    `expires_at` = persisted_at + the STORE's chunk TTL; the stored bytes are counted on the
    job and the first committed chunk sets `jobs.published` in the same transaction."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        assert not cl.row(conn, request.request_id)["published"], "published before output"
        advance(conn, 7)                        # the chunk's instants are the append's own
        now = db_now(conn)
        code, answer = append(conn, lease, b.events("Hello", " world"))
        assert code is None, f"a live lease's append was refused: {code}"
        rows = journal(conn, request.request_id)
        assert chunks(answer["chunks"]) == chunks(rows), "the answer is not the committed rows"
        assert [(r["generation"], r["sequence"], r["event_type"], r["payload"]) for r in rows] \
            == [(1, 1, "delta", {"content": "Hello"}), (1, 2, "delta", {"content": " world"})], \
            rows
        assert [r["bytes"] for r in rows] == [size(conn, r["payload"]) for r in rows], \
            "bytes are not the stored text's measure"
        assert all(r["persisted_at"] == now and
                   r["expires_at"] == now + timedelta(seconds=CHUNK_TTL) for r in rows), \
            f"the chunk instants are not the database clock at the append: {rows}"
        job = cl.row(conn, request.request_id)
        assert job["published"], "the first committed chunk did not set the publication marker"
        assert job["journal_stored_bytes"] == sum(r["bytes"] for r in rows), \
            "the stored bytes were not counted on the job"
        # the next batch continues the generation; its TTL is the store's configuration
        advance(conn, 3)
        now = db_now(conn)
        code, answer = append(conn, lease, b.events("!"), limits=SHORT)
        assert code is None, code
        assert [(c["generation"], c["sequence"]) for c in answer["chunks"]] == [(1, 3)], \
            f"the sequence did not continue the generation: {answer}"
        rows = journal(conn, request.request_id)
        assert rows[-1]["expires_at"] == now + timedelta(seconds=SHORT.journal_chunk_ttl_s), \
            "the chunk TTL is not the store's"
        assert cl.row(conn, request.request_id)["journal_stored_bytes"] == \
            sum(r["bytes"] for r in rows), "the second batch's bytes were not counted"
        return "one transaction per batch; answer = committed rows; DB clock, store TTL"
    return ca._in_rollback(conn, body)


def check_append_oversize(conn) -> str:
    """R25 / DUR-OUTPUT (dr10): one event over `journal_event_max_bytes`, wherever it is in
    the batch, is `journal_write_failed` and NOTHING of the batch is stored or published.
    Pinned on the stored measure at the default limit: exactly the limit is accepted, one
    byte more refused - and that measure exceeds the fake's `compact_bytes` by jsonb's
    separator (one byte for the `": "` of a one-key payload)."""
    world = ca.World(conn)
    small = DEFAULTS.replace(journal_event_max_bytes=64)

    def body():
        request, lease = running(conn, world)
        big = event({"content": "x" * 200})
        for batch in ((big,), (*b.events("a"), big), (big, *b.events("a")),
                      (*b.events("a"), big, *b.events("b"))):
            code, _ = append(conn, lease, batch, limits=small)
            assert code == "journal_write_failed", f"an oversize event in a batch: {code}"
        job = cl.row(conn, request.request_id)
        assert journal(conn, request.request_id) == [] and not job["published"] and \
            job["journal_stored_bytes"] == 0, "a refused batch stored or published a prefix"
        limit = DEFAULTS.journal_event_max_bytes
        exact = sized(conn, limit)
        code, _ = append(conn, lease, (event(sized(conn, limit + 1)),))
        assert code == "journal_write_failed", f"limit + 1 bytes: {code}"
        code, answer = append(conn, lease, (event(exact),))
        assert code is None, f"exactly the limit was refused: {code}"
        assert [c["bytes"] for c in answer["chunks"]] == [limit], answer["chunks"][0]["bytes"]
        compact = len(compact_bytes(exact))
        assert size(conn, exact) == compact + 1, (size(conn, exact), compact)
        return f"limit {limit} accepted, {limit + 1} refused (fake compact measure {compact})"
    return ca._in_rollback(conn, body)


def check_append_terminal_refused(conn) -> str:
    """R30: a worker's batch holding a `terminal` event ANYWHERE - alone, first, last or in
    the middle - is `invalid_request` and stores nothing; so is an unknown event type or a
    non-object payload. The real terminalization then writes exactly one terminal event, its
    own."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        forged = EngineEvent(type=ChunkEventType.terminal, payload={
            "state": "succeeded", "cause": "completed", "settlement_state": "settled"})
        for batch in ((forged,), (*b.events("a"), forged), (forged, *b.events("b")),
                      (*b.events("a"), forged, *b.events("b"))):
            code, _ = append(conn, lease, batch)
            assert code == "invalid_request", f"a batch with a terminal event: {code}"
        for docs in ([{"type": "heartbeat", "payload": {}}], [{"type": "delta", "payload": "x"}],
                     [{"type": "delta"}]):
            code, _ = append_docs(conn, lease, docs)
            assert code == "invalid_request", f"{docs}: {code}"
        assert journal(conn, request.request_id) == [] and \
            not cl.row(conn, request.request_id)["published"], "a refused batch left a trace"
        code, done = cancel(conn, request)
        assert code is None, code
        terminal = one_terminal_last(conn, request.request_id, "cancel after refused batches")
        assert terminal["payload"]["cause"] == "client_cancelled", terminal
        return "terminal/unknown events refused anywhere in a batch; one real terminal event"
    return ca._in_rollback(conn, body)


def check_append_job_ceiling(conn) -> str:
    """DUR-CAP / R39: an append may fill the job's reservation EXCEPT the bytes held back for
    the terminal event (least(1024, reservation / 2)): exactly that ceiling is accepted, one
    byte more is `journal_capacity_exhausted`. The terminal event then uses the held-back
    bytes. A terminalization whose held-back bytes cannot fit the WIDEST terminal payload
    (every state x cause x settlement, < 1024) is refused whole - cancel answers
    `journal_capacity_exhausted` and changes nothing, the reaper reports the job
    unsettleable - and it settles once pruning makes room (the fake's
    `check_terminal_capacity`)."""
    world = ca.World(conn)
    widest = widest_terminal(conn)
    assert 0 < widest < 1024, f"the widest terminal payload {widest} does not fit 1024 bytes"

    def body():
        limits = DEFAULTS.replace(journal_job_reserve_bytes=8192)
        request, lease = running(conn, world, limits=limits)
        ceiling = 8192 - 1024
        code, _ = append(conn, lease, (event(sized(conn, ceiling - 2)),))
        assert code is None, code
        code, _ = append(conn, lease, (event({}),))          # 2 bytes: exactly the ceiling
        assert code is None, f"an append up to the ceiling was refused: {code}"
        assert cl.row(conn, request.request_id)["journal_stored_bytes"] == ceiling
        code, _ = append(conn, lease, (event({}),))
        assert code == "journal_capacity_exhausted", f"an append past the ceiling: {code}"
        assert cl.row(conn, request.request_id)["journal_stored_bytes"] == ceiling and \
            len(journal(conn, request.request_id)) == 2, "a refused append stored something"
        assert cancel(conn, request)[0] is None, "the terminal event did not fit the reserve"
        terminal = one_terminal_last(conn, request.request_id, "cancel at the ceiling")
        assert ceiling + terminal["bytes"] <= 8192
        # the held-back bytes must fit the WIDEST terminal payload, not just this one
        for reservation, fits in ((2 * widest, True), (2 * widest - 1, False)):
            tight = DEFAULTS.replace(journal_job_reserve_bytes=reservation)
            job, lease = running(conn, world, limits=tight, worker=f"w{reservation}")
            code, _ = append(conn, lease, (event(sized(conn, reservation - min(
                1024, reservation // 2))),))
            assert code is None, f"reservation {reservation}: the fill was refused: {code}"
            before = (cl.reserved(conn), kinds(conn, job.request_id))
            code, _ = cancel(conn, job)
            if fits:
                assert code is None, f"reservation {reservation}: the widest fit, yet {code}"
                one_terminal_last(conn, job.request_id, f"reservation {reservation}")
                continue
            assert code == "journal_capacity_exhausted", \
                f"reservation {reservation}: a terminal event past the reservation: {code}"
            assert cl.row(conn, job.request_id)["state"] == "running" and \
                (cl.reserved(conn), kinds(conn, job.request_id)) == before and \
                [r["event_type"] for r in journal(conn, job.request_id)] == ["delta"], \
                "a refused terminalization changed something"
            advance(conn, TTL)
            produced = cl._recover(conn)
            stuck = [i["unsettleable"] for i in produced if "unsettleable" in i]
            assert [s["job_id"] for s in stuck] == [job.request_id] and \
                "journal_capacity_exhausted" in stuck[0]["detail"], produced
            advance(conn, CHUNK_TTL)
            assert expire(conn)[1] >= 1
            cl._recover(conn)
            assert cl.row(conn, job.request_id)["settled_at"] is not None, \
                "the stuck job never settled once pruning made room"
            one_terminal_last(conn, job.request_id, "settled after pruning")
        return f"ceiling = reservation - held back; widest terminal payload {widest} bytes"
    return ca._in_rollback(conn, body)


def check_append_empty(conn) -> str:
    """DUR-OUTPUT: an empty batch commits nothing and does not publish, so it cannot forbid
    a prepublication requeue: the lost attempt is still requeued, never failed
    `lost_after_publication`."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        code, answer = append(conn, lease, ())
        assert code is None and answer == {"chunks": []}, (code, answer)
        job = cl.row(conn, request.request_id)
        assert journal(conn, request.request_id) == [] and not job["published"] and \
            job["journal_stored_bytes"] == 0, "an empty batch wrote or published"
        advance(conn, TTL)
        cl.reaped(cl._recover(conn), "index_event")
        assert cl.row(conn, request.request_id)["state"] == "queued", \
            "an empty batch forbade the prepublication requeue"
        return "an empty batch writes nothing and publishes nothing"
    return ca._in_rollback(conn, body)


def check_append_fenced(conn) -> str:
    """DUR-FENCE: append takes an INFERENCE lease through the fence (R46). A preparation
    token, a foreign worker at the same generation, a stale generation, an unknown job and an
    expired lease are refused and store nothing; a live PREPARATION lease on a preparing job
    stores nothing; a terminal job is `already_terminal`, its journal only its terminal
    event."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        forged = {"a preparation token": (cl.dump(lease, kind=LeaseKind.preparation,
                                                  first_token_deadline_at=None), "stale_lease"),
                  "a foreign worker": (cl.dump(lease, worker_id="w2"), "stale_lease"),
                  "a stale generation": (cl.dump(lease, generation=2), "stale_lease"),
                  "an unknown job": (cl.dump(lease, job_id=str(uuid.uuid4())), "not_found")}
        for label, (token, expected) in forged.items():
            code, _ = outcome(conn, "append", {**args(lease, b.events("forged")),
                                               "lease": token})
            assert code == expected, f"{label}: {code}"
        preparing = cl.gateway_request(world)
        ca.admit(conn, preparing, b.idem(preparing, preparing.request_id))
        _, prep = claim(conn, preparing.request_id)
        code, _ = outcome(conn, "append", {**args(lease, b.events("prepared?")),
                                           "lease": prep["lease"]})
        assert code == "stale_lease", f"a preparation lease appended: {code}"
        assert journal(conn, preparing.request_id) == [] and \
            not cl.row(conn, preparing.request_id)["published"]
        advance(conn, TTL)                               # exactly at the lease's expires_at
        code, _ = append(conn, lease, b.events("late"))
        assert code == "stale_lease", f"an expired lease appended: {code}"
        job = cl.row(conn, request.request_id)
        assert journal(conn, request.request_id) == [] and not job["published"] and \
            job["state"] == "running", "a fenced-out append left a trace"
        done, live = running(conn, world, worker="w-done")
        assert cancel(conn, done)[0] is None
        code, _ = append(conn, live, b.events("after the end"))
        assert code == "already_terminal", f"an append to a terminal job: {code}"
        assert [r["event_type"] for r in journal(conn, done.request_id)] == ["terminal"]
        return "every non-holder is fenced out of append; nothing stored"
    return ca._in_rollback(conn, body)


def check_append_past_the_instant(conn) -> str:
    """R29/R39: past the generation instant, lease still LIVE, append terminalizes the job
    in the same call (`deadline_exceeded`), COMMITS it and answers `already_terminal`: the
    late batch is not stored, the terminal event is last, and the settlement follows
    publication (unpublished: released, the hold returned; published: held_unknown)."""
    world = ca.World(conn)

    def body():
        for published in (False, True):
            request, lease = running(conn, world, worker=f"w-{published}")
            if published:
                assert append(conn, lease, b.events("first"))[0] is None
            conn.execute("update infrx.attempts set expires_at = expires_at + interval '1 day' "
                         "where job_id = %s", (request.request_id,))
            before = cl.reserved(conn)
            advance(conn, DEFAULTS.generation_timeout_s)
            code, _ = append(conn, lease, b.events("too late"))
            assert code == "already_terminal", f"published={published}: {code}"
            job = cl.row(conn, request.request_id)
            settlement = "held_unknown" if published else "released_platform_absorbed"
            assert (job["state"], job["outcome_cause"], job["settlement_state"], job["debit"]) \
                == ("failed", "deadline_exceeded", settlement, 0), (published, job)
            assert [r["event_type"] for r in journal(conn, request.request_id)] == \
                ["delta"] * published + ["terminal"], journal(conn, request.request_id)
            one_terminal_last(conn, request.request_id, f"R29 append published={published}")
            if not published:
                assert cl.reserved(conn) == before - job["maximum_hold"], "hold not released"
        return "R29 terminalizes in the append, commits, refuses; the batch is not stored"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 2: the budget
def check_global_charge(conn) -> str:
    """DUR-CAP without a global lock: with the global budget full of reservations, an append
    up to its job's ceiling leaves `journal_bytes_charged()` unchanged and the next admission
    is still refused; one byte past the ceiling is refused without moving it. After the
    terminalization the job's stored bytes (its output + the terminal event) keep counting -
    no admission fits - and pruning frees them exactly: never counted twice."""
    world = ca.World(conn)
    limits = DEFAULTS.replace(journal_job_reserve_bytes=8192, journal_total_bytes=16384)

    def admitted():
        request = cl.gateway_request(world)
        return request, ca.refusal(conn, request, b.idem(request, request.request_id),
                                   limits=limits)

    def body():
        assert charged(conn) == 0, "the scenario already holds journal bytes"
        request, lease = running(conn, world, limits=limits)
        other, code = admitted()
        assert code is None, code
        assert charged(conn) == 16384
        assert admitted()[1] == "journal_capacity_exhausted", "the full budget admitted"
        assert append(conn, lease, (event(sized(conn, 8192 - 1024)),))[0] is None
        assert charged(conn) == 16384, "an append moved the global charge"
        code, _ = append(conn, lease, (event(sized(conn, 1025)),))
        assert code == "journal_capacity_exhausted", f"past the reservation: {code}"
        assert charged(conn) == 16384, "a refused append moved the global charge"
        assert admitted()[1] == "journal_capacity_exhausted"
        assert cancel(conn, request)[0] is None
        stored = cl.row(conn, request.request_id)["journal_stored_bytes"]
        assert stored == sum(r["bytes"] for r in journal(conn, request.request_id)) > 8192 - 1024
        assert charged(conn) == 8192 + stored, "terminal: reservation freed, stored counting"
        assert admitted()[1] == "journal_capacity_exhausted", \
            "stored unexpired bytes stopped counting before they were pruned"
        advance(conn, CHUNK_TTL)
        pruned = len(journal(conn, request.request_id))
        assert expire(conn) == (None, pruned), "the terminal job's journal was not pruned"
        assert cl.row(conn, request.request_id)["journal_stored_bytes"] == 0
        assert charged(conn) == 8192, "pruning did not free the bytes exactly"
        assert admitted()[1] is None, "freed bytes still refuse an admission"
        assert charged(conn) == 16384
        return "appends never move the global charge; stored bytes count until pruned, once"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 3: terminal
def check_terminal_every_path(conn) -> str:
    """R30 / DUR-OUTPUT: every terminalization writes exactly ONE terminal event, last in
    cursor order, from the stored row, in its own transaction - 0012's preparation failure,
    cancel before and after publication, R29 through heartbeat, load_work and append, the
    reaper's retries_exhausted / lost_after_publication / queue_wait_expired /
    preparation_failed, a second generation's journal; the 24 h release writes no second
    event; a CREDIT job the same."""
    world = ca.World(conn)

    def admitted():
        request = cl.gateway_request(world)
        ca.admit(conn, request, b.idem(request, request.request_id))
        return request

    def preparation_claim():
        request = admitted()
        advance(conn, DEFAULTS.preparation_timeout_s)
        assert claim(conn, request.request_id)[0] == "already_terminal"
        return request, "preparation_failed"

    def cancel_unpublished():
        request, _ = running(conn, world)
        assert cancel(conn, request)[0] is None
        return request, "client_cancelled"

    def cancel_published():
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("a", "b"))[0] is None
        assert cancel(conn, request)[0] is None
        assert cl.row(conn, request.request_id)["settlement_state"] == "held_unknown"
        return request, "client_cancelled"

    def overdue(fn):
        def path():
            request, lease = running(conn, world)
            assert append(conn, lease, b.events("a"))[0] is None
            conn.execute("update infrx.attempts set expires_at = expires_at + "
                         "interval '1 day' where job_id = %s", (request.request_id,))
            advance(conn, DEFAULTS.generation_timeout_s)
            if fn == "append":
                code, _ = append(conn, lease, b.events("late"))
            else:
                code, _ = cl.d3(conn, fn, lease=lease.model_dump(mode="json"))
            assert code == "already_terminal", f"{fn}: {code}"
            return request, "deadline_exceeded"
        return path

    def retries_exhausted():
        request = cl.queued(conn, world)
        for _ in range(DEFAULTS.max_prepublication_retries + 1):
            assert cl.d3(conn, "claim", job_id=request.request_id, worker_id="w1")[0] is None
            advance(conn, TTL)
            cl._recover(conn)
        return request, "retries_exhausted"

    def lost_after_publication():
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("a"))[0] is None
        advance(conn, TTL)
        cl._recover(conn)
        return request, "lost_after_publication"

    def queue_wait_expired():
        request = cl.queued(conn, world)
        advance(conn, DEFAULTS.queue_wait_interactive_s)
        cl._recover(conn)
        return request, "queue_wait_expired"

    def preparation_reaped():
        request = admitted()
        advance(conn, DEFAULTS.preparation_timeout_s)
        cl._recover(conn)
        return request, "preparation_failed"

    def second_generation():
        request, _ = running(conn, world)
        advance(conn, TTL)
        cl.reaped(cl._recover(conn), "index_event")
        code, answer = cl.d3(conn, "claim", job_id=request.request_id, worker_id="w1")
        assert code is None and answer["lease"]["generation"] == 2, (code, answer)
        assert append(conn, cl.lease_of(answer), b.events("x", "y"))[0] is None
        assert cancel(conn, request)[0] is None
        assert cursors(journal(conn, request.request_id))[-1][:2] == (2, 3), \
            f"not after the second generation: {cursors(journal(conn, request.request_id))}"
        return request, "client_cancelled"

    def credit():
        request, lease = cl.credit_running(conn, world)
        assert append(conn, lease, b.events("c"))[0] is None
        assert cancel(conn, request)[0] is None
        return request, "client_cancelled"

    paths = {"0012 preparation failure": preparation_claim,
             "cancel before publication": cancel_unpublished,
             "cancel after publication": cancel_published,
             "R29 via heartbeat": overdue("heartbeat"), "R29 via load_work": overdue("load_work"),
             "R29 via append": overdue("append"), "recover retries_exhausted": retries_exhausted,
             "recover lost_after_publication": lost_after_publication,
             "recover queue_wait_expired": queue_wait_expired,
             "recover preparation_failed": preparation_reaped,
             "a second generation's journal": second_generation, "a CREDIT job": credit}
    for what, path in paths.items():
        def run(path=path, what=what):
            request, cause = path()
            terminal = one_terminal_last(conn, request.request_id, what)
            assert terminal["payload"]["cause"] == cause, (what, terminal["payload"])
        ca._in_rollback(conn, run)

    def released():
        request, cause = cancel_published()
        advance(conn, DEFAULTS.unknown_usage_reconcile_s)
        cl._recover(conn)
        assert cl.row(conn, request.request_id)["settlement_state"] == \
            "released_platform_absorbed", "the 24 h release did not run"
        one_terminal_last(conn, request.request_id, "the 24 h release",
                          settlement="held_unknown")
    ca._in_rollback(conn, released)
    return f"{len(paths)} terminalization paths, one terminal event each; 24 h release none"


# --------------------------------------------------------------------- item 4: replay
def pages(conn, request, limit: int, cursor=None) -> list[dict]:
    """Every chunk after `cursor`, read `limit` at a time until an empty page."""
    got = []
    while True:
        code, answer = read(conn, request, cursor, limit)
        assert code is None, f"a replay page was refused: {code}"
        if not answer["chunks"]:
            return got
        got += answer["chunks"]
        cursor = at(got[-1]["generation"], got[-1]["sequence"])


def check_read_replay(conn) -> str:
    """DUR-OUTPUT / API-STREAM: replay equals the committed rows - event type, payload,
    cursor, bytes, persisted_at, expires_at - whatever the page size, in (generation,
    sequence) order. A journal holding two generations (a fixture: the protocol publishes on
    the first chunk and never requeues after it) is still never spliced."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        committed = []
        for batch in (("a", "b", "c"), ("d",), ("e", "f")):
            code, answer = append(conn, lease, b.events(*batch))
            assert code is None, code
            committed += answer["chunks"]
        rows = journal(conn, request.request_id)
        assert chunks(committed) == chunks(rows)
        for limit in (1, 2, 4, 100):
            assert chunks(pages(conn, request, limit)) == chunks(rows), \
                f"a replay in pages of {limit} is not the committed rows"
        conn.execute("insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
                     "payload, bytes, committed_at, expires_at) select %s, 2, s, 'delta', "
                     "'{}'::jsonb, 2, infrx.now(), infrx.now() + interval '1 hour' "
                     "from generate_series(1, 2) s", (request.request_id,))
        rows = journal(conn, request.request_id)
        for limit in (1, 2, 4, 100):
            got = pages(conn, request, limit)
            assert cursors(got) == cursors(rows) == \
                [(1, n, "delta") for n in range(1, 7)] + [(2, 1, "delta"), (2, 2, "delta")], \
                f"pages of {limit} spliced the generations: {cursors(got)}"
        return "replay = committed rows, in (generation, sequence) order, any page size"
    return ca._in_rollback(conn, body)


def check_read_tenant(conn) -> str:
    """R10 (dr08): the journal is read by (org, handle). Another organization's read of this
    handle and anyone's read of an unknown handle are the same `not_found`."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("private"))[0] is None
        assert read(conn, request, org=b.ORG_B)[0] == "not_found", "another org replayed it"
        assert read(conn, request, handle="job_nope")[0] == "not_found"
        code, answer = read(conn, request)
        assert code is None and [c["payload"] for c in answer["chunks"]] == \
            [{"content": "private"}], (code, answer)
        return "another tenant's handle reads as not_found, like an unknown one"
    return ca._in_rollback(conn, body)


def check_read_bounded(conn) -> str:
    """API-STREAM: a limit below 1 is `invalid_request`; a page is at most 1000 chunks
    (MAX_READ_LIMIT) whatever the caller asks; the rest follows from its cursor."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        assert append(conn, lease, tuple(event({}) for _ in range(1001)))[0] is None
        for bad in (0, -1):
            assert read(conn, request, limit=bad)[0] == "invalid_request", f"limit {bad}"
        code, answer = read(conn, request, limit=5000)
        assert code is None and len(answer["chunks"]) == 1000, \
            f"a page of {len(answer['chunks'])} chunks"
        code, answer = read(conn, request, at(1, 1000), 5000)
        assert code is None and cursors(answer["chunks"]) == [(1, 1001, "delta")], answer
        code, answer = read(conn, request, None, 2)
        assert code is None and cursors(answer["chunks"]) == [(1, 1, "delta"), (1, 2, "delta")]
        return "limits below 1 refused; pages capped at 1000"
    return ca._in_rollback(conn, body)


def check_read_typed(conn) -> str:
    """API-STREAM / DUR-OUTPUT: nothing is silent. At the head: an empty page. Past it (a
    cursor the journal never issued): `invalid_cursor`. Below the prune watermark:
    `replay_gap`, never the rows after it. Nothing left: `journal_expired`, never an empty
    page."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        code, answer = read(conn, request)
        assert code is None and answer["chunks"] == [], "an empty journal is an empty page"
        assert read(conn, request, at(1, 1))[0] == "invalid_cursor"
        assert append(conn, lease, b.events("a", "b"), limits=SHORT)[0] is None
        code, answer = read(conn, request, at(1, 2))
        assert code is None and answer["chunks"] == [], "the head is simply nothing new"
        for beyond in (at(1, 3), at(2, 1), at(9, 9)):
            assert read(conn, request, beyond)[0] == "invalid_cursor", beyond
        advance(conn, SHORT.journal_chunk_ttl_s + 1)
        assert append(conn, lease, b.events("c"), limits=SHORT)[0] is None
        assert expire(conn) == (None, 2)
        for below in (None, at(1, 0), at(1, 1)):
            assert read(conn, request, below)[0] == "replay_gap", f"{below}: a silent gap"
        code, answer = read(conn, request, at(1, 2))
        assert code is None and [c["payload"] for c in answer["chunks"]] == [{"content": "c"}]
        advance(conn, SHORT.journal_chunk_ttl_s + 1)
        assert expire(conn) == (None, 1)
        for cursor in (None, at(1, 3)):
            assert read(conn, request, cursor)[0] == "journal_expired", \
                f"{cursor}: an expired journal read as something else"
        return "head empty, past it invalid_cursor, below the watermark replay_gap, gone expired"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 5: pruning
def check_expire_clock(conn) -> str:
    """R7: `expire` prunes on the database clock; a caller's time is a bound at most - a year
    ahead prunes nothing live, a time in the past prunes nothing - and a chunk goes exactly
    at its `expires_at`, not a second before."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("still live"))[0] is None
        now = db_now(conn)
        for when in (now + timedelta(days=365), now - timedelta(days=1)):
            assert expire(conn, when) == (None, 0), f"expire({when}) pruned a live chunk"
        advance(conn, CHUNK_TTL - 1)
        assert expire(conn) == (None, 0), "a chunk expired before its TTL"
        advance(conn, 1)
        assert expire(conn) == (None, 1), "a chunk outlived its expires_at"
        assert journal(conn, request.request_id) == []
        return "pruning follows the database clock only"
    return ca._in_rollback(conn, body)


def check_expire_prefix(conn) -> str:
    """DUR-OUTPUT: pruning removes a PREFIX and persists its watermark. A chunk committed
    earlier but expiring later (two appends' clocks) goes with the prefix, so the watermark
    only grows and a cursor below it is always a gap - never the rows after a silently
    missing one. When nothing is left the journal is expired; a later chunk (an append, or
    the terminal event) lands past the watermark and is readable from it."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("a", "b"), limits=SHORT)[0] is None
        advance(conn, 31)
        assert append(conn, lease, b.events("c", "d", "e", "f"), limits=SHORT)[0] is None
        assert expire(conn) == (None, 2)
        job = cl.row(conn, request.request_id)
        assert (job["journal_pruned_generation"], job["journal_pruned_sequence"]) == (1, 2)
        assert read(conn, request, at(1, 0))[0] == "replay_gap"
        # d (1,4) expires 30 s after e (1,5): committed earlier on a later clock (the skew
        # two concurrent appends can have); f (1,6) lives 60 s longer
        conn.execute("update infrx.stream_chunks set expires_at = expires_at + case sequence "
                     "when 4 then interval '30 seconds' when 6 then interval '60 seconds' "
                     "else interval '0' end where job_id = %s", (request.request_id,))
        advance(conn, 31)
        assert expire(conn) == (None, 3), "the pruned set was not the prefix up to e"
        advance(conn, 31)
        assert expire(conn) == (None, 0)
        job = cl.row(conn, request.request_id)
        assert (job["journal_pruned_generation"], job["journal_pruned_sequence"]) == (1, 5), \
            "the watermark moved backwards"
        assert read(conn, request, at(1, 4))[0] == "replay_gap", "a pruned chunk was skipped"
        code, answer = read(conn, request, at(1, 5))
        assert code is None and cursors(answer["chunks"]) == [(1, 6, "delta")], answer
        assert cl.d3(conn, "heartbeat", lease=lease.model_dump(mode="json"))[0] is None
        advance(conn, 31)
        assert expire(conn) == (None, 1)
        assert read(conn, request)[0] == "journal_expired"
        # the live attempt appends again: past the watermark, never a reissued cursor
        code, answer = append(conn, lease, b.events("g"))
        assert code is None and [(c["generation"], c["sequence"]) for c in answer["chunks"]] \
            == [(1, 7)], f"a pruned cursor was reissued: {code} {answer}"
        code, answer = read(conn, request, at(1, 6))
        assert code is None and cursors(answer["chunks"]) == [(1, 7, "delta")], answer
        # a journal expired while running: the terminal event lands past the watermark too
        other, lease = running(conn, world, worker="w2")
        assert append(conn, lease, b.events("x"), limits=SHORT)[0] is None
        advance(conn, 31)
        assert expire(conn)[0] is None and read(conn, other)[0] == "journal_expired"
        assert cancel(conn, other)[0] is None
        assert read(conn, other)[0] == "replay_gap"
        code, answer = read(conn, other, at(1, 1))
        assert code is None and cursors(answer["chunks"]) == [(1, 2, "terminal")], answer
        return "a prefix per pass, the watermark only grows, later chunks land past it"
    return ca._in_rollback(conn, body)


def check_expire_bytes(conn) -> str:
    """DUR-CAP: the stored bytes of a journal whose LAST chunk is the terminal event are the
    sum of its chunks, and pruning frees exactly the pruned chunks' bytes, once: a partial
    prune leaves the rest counted, the final prune reaches zero, and a further pass frees
    nothing."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("a", "b"), limits=SHORT)[0] is None
        advance(conn, 20)
        assert append(conn, lease, b.events("c"), limits=SHORT)[0] is None
        assert cancel(conn, request)[0] is None
        one_terminal_last(conn, request.request_id, "the pruned journal")
        rows = journal(conn, request.request_id)
        stored = lambda: cl.row(conn, request.request_id)["journal_stored_bytes"]  # noqa: E731
        assert stored() == sum(r["bytes"] for r in rows)
        advance(conn, 11)
        assert expire(conn) == (None, 2)
        assert stored() == sum(r["bytes"] for r in rows[2:]), \
            f"a partial prune freed {sum(r['bytes'] for r in rows) - stored()} bytes"
        advance(conn, 20)
        assert expire(conn) == (None, 2)
        assert stored() == 0, f"{stored()} bytes left after the whole journal was pruned"
        assert expire(conn) == (None, 0) and stored() == 0, "a second pass freed again"
        return "pruning frees exactly the pruned bytes, once"
    return ca._in_rollback(conn, body)


def check_usage(conn) -> str:
    """G1R request 1: `infrx.journal_usage()` reports the live reservations, the stored bytes,
    the charge admission checks - per job the larger of its live reservation and its stored
    bytes, never both - and the chunk count."""
    world = ca.World(conn)

    def truth() -> dict:
        per_job = conn.execute(
            "select coalesce(r.amount, 0), j.journal_stored_bytes from infrx.jobs j "
            "left join infrx.capacity_reservations r on r.request_id = j.request_id "
            "and r.kind = 'journal_bytes' and r.active").fetchall()
        return {"reserved_bytes": sum(r for r, _ in per_job),
                "stored_bytes": sum(s for _, s in per_job),
                "charged_bytes": sum(max(r, s) for r, s in per_job),
                "chunks": conn.execute("select count(*) from infrx.stream_chunks").fetchone()[0]}

    def usage() -> dict:
        return conn.execute("select infrx.journal_usage()").fetchone()[0]

    def body():
        running(conn, world, worker="idle")
        request, lease = running(conn, world)
        assert append(conn, lease, b.events("a", "b"))[0] is None
        before = usage(), truth()
        assert cancel(conn, request)[0] is None
        after = usage(), truth()
        for got, want in (before, after):
            assert got == want, f"journal_usage {got} != {want}"
        reserve = DEFAULTS.journal_job_reserve_bytes
        assert before[0]["charged_bytes"] == 2 * reserve and after[0]["charged_bytes"] == \
            reserve + after[0]["stored_bytes"] and after[0]["chunks"] == 3, (before, after)
        return "usage = reservations, stored bytes, the admission charge, chunks"
    return ca._in_rollback(conn, body)
