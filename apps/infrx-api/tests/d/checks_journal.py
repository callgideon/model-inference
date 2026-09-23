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
